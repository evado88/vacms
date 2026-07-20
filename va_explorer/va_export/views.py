import logging
import re

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import F, Q
from django.http import JsonResponse
from django.views.generic import View
from django.views.generic.edit import FormView

from va_explorer.users.utils.location_mapping import map_srs_clusters_to_locations
from va_explorer.utils.mixins import CustomAuthMixin
from va_explorer.va_data_management.constants import PII_FIELDS, REDACTED_STRING
from va_explorer.va_data_management.models import (
    CSADailyTracker,
    Death,
    Household,
    Location,
    ODKFormChoice,
    Pregnancy,
    PregnancyOutcome,
    SRSClusterLocation,
    VerbalAutopsy,
)
from va_explorer.va_export.forms import ExportRequestForm, VADownloadForm
from va_explorer.va_export.profiles import ABRIDGED_VA_EXPORT_COLUMNS
from va_explorer.va_export.streaming import (
    stream_csv_zip_response,
    stream_json_zip_response,
)

logger = logging.getLogger(__name__)

_GEO_LEVELS = {"province", "district", "constituency", "ward", "ea"}
_EXPORT_PII_FIELDS = frozenset(
    [
        *PII_FIELDS,
        "geopoint",
        "hh_gps",
        "gps",
        "hh_gps_latitude",
        "hh_gps_longitude",
        "hh_gps_altitude",
        "hh_gps_accuracy",
    ]
)
_VA_ANNOTATION_COLUMNS = [
    "date",
    "cause",
    "cause_id",
    "cause_algorithm",
    "cause_created",
]
_VA_QUERY_HELPER_COLUMNS = ["loc_name", "cluster_name"]

_RUMOR_SURVEILLANCE_COLUMNS = [
    "id",
    "key",
    "submissiondate",
    "instanceid",
    "instancename",
    "submitterid",
    "submittername",
    "formversion",
    "deviceid",
    "today",
    "start",
    "province",
    "district",
    "constituency",
    "ward",
    "ea",
    "enumerator",
    "hh_gps_latitude",
    "hh_gps_longitude",
    "hh_gps_altitude",
    "hh_gps_accuracy",
    "rum",
    "sorce_rum",
    "other_rum",
    "desci",
    "ru_num",
]


def _log_export(request, dataset, row_count, fmt):
    """Record a data-export audit entry. Never raises (see UserActivity.log)."""
    from va_explorer.users.models import UserActivity

    UserActivity.log(
        UserActivity.DATA_EXPORT,
        user=getattr(request, "user", None),
        request=request,
        module=dataset,
        target_type=dataset,
        details=f"Exported {row_count} {dataset} record(s) as {fmt}.",
        extra={"format": fmt, "rows": int(row_count)},
    )


def _model_export_columns(model):
    return [
        field.attname
        for field in model._meta.concrete_fields
        if field.attname != "index"
    ]


def _redact_row(row, can_view_pii):
    if can_view_pii:
        return row
    for field in _EXPORT_PII_FIELDS:
        if field in row:
            row[field] = REDACTED_STRING
    return row


def _iter_queryset_rows(queryset, columns, *, transform=None, chunk_size=1000):
    rows = queryset.values(*columns).iterator(chunk_size=chunk_size)
    for row in rows:
        yield transform(row) if transform else row


def _audit_rows(rows, request, dataset, fmt):
    """Log a successful export only after its row iterator is fully consumed."""

    row_count = 0
    completed = False
    try:
        for row in rows:
            row_count += 1
            yield row
        completed = True
    finally:
        if completed:
            _log_export(request, dataset, row_count, fmt)


def _tree_geography_lookup(model, *, include_self):
    """Build all ancestor geography labels in one query for MP tree models."""

    nodes = list(
        model.objects.values("id", "path", "name", "location_type").order_by("path")
    )
    by_path = {node["path"]: node for node in nodes}
    step_length = model.steplen
    lookup = {}
    for node in nodes:
        path_length = len(node["path"])
        stop = path_length if include_self else path_length - step_length
        geography = {}
        for prefix_length in range(step_length, stop + 1, step_length):
            ancestor = by_path.get(node["path"][:prefix_length])
            if not ancestor:
                continue
            level = str(ancestor["location_type"] or "").lower()
            if level in _GEO_LEVELS and ancestor["name"]:
                geography[level] = ancestor["name"]
        lookup[node["id"]] = geography
    return lookup


def _minimal_tree_nodes(nodes):
    selected = []
    for node in sorted(nodes, key=lambda item: len(item.path)):
        if not any(node.path.startswith(parent.path) for parent in selected):
            selected.append(node)
    return selected


def _tree_geography_branches(model, nodes, field_mapping):
    nodes = _minimal_tree_nodes(list(nodes))
    if not nodes:
        return []

    geography_lookup = _tree_geography_lookup(model, include_self=True)
    branches = []
    for node in nodes:
        selected_level = str(node.location_type or "").strip().lower()
        if selected_level not in field_mapping:
            continue
        geography = geography_lookup.get(node.pk, {})
        branch = {
            level: geography[level] for level in field_mapping if geography.get(level)
        }
        if branch:
            branches.append(branch)
    return branches


def _geography_branches_q(branches, field_mapping):
    geography_filter = Q()
    for branch in branches:
        branch_filter = Q()
        for level, name in branch.items():
            branch_filter &= Q(**{f"{field_mapping[level]}__iexact": name})
        if branch_filter.children:
            geography_filter |= branch_filter
    return geography_filter


def _selected_srs_geography_branches(location_ids, field_mapping):
    if not location_ids:
        return []
    selected = SRSClusterLocation.objects.filter(pk__in=location_ids)
    return _tree_geography_branches(
        SRSClusterLocation,
        selected,
        field_mapping,
    )


def _build_geo_filter_from_srs(loc_query, field_mapping):
    if not loc_query:
        return Q()
    id_list = [int(pk) for pk in str(loc_query).split(",") if pk]
    if not id_list:
        return Q()
    branches = _selected_srs_geography_branches(id_list, field_mapping)
    return _geography_branches_q(branches, field_mapping)


def _user_location_nodes(user):
    restrictions = getattr(user, "location_restrictions", None)
    if restrictions is None:
        return None
    nodes = list(restrictions.all())
    return _minimal_tree_nodes(nodes) if nodes else None


def _restrict_export_queryset_to_user_locations(queryset, user, field_mapping):
    nodes = _user_location_nodes(user)
    if nodes is None:
        return queryset
    branches = _tree_geography_branches(Location, nodes, field_mapping)
    geography_filter = _geography_branches_q(branches, field_mapping)
    return (
        queryset.filter(geography_filter)
        if geography_filter.children
        else queryset.none()
    )


def _restrict_va_export_queryset_to_user_locations(queryset, user):
    nodes = _user_location_nodes(user)
    if nodes is None:
        return queryset

    relation_filter = Q()
    for node in nodes:
        relation_filter |= Q(location__path__startswith=node.path)

    field_mapping = {level: level for level in _GEO_LEVELS}
    branches = _tree_geography_branches(Location, nodes, field_mapping)
    geography_filter = _geography_branches_q(branches, field_mapping)
    if geography_filter.children:
        relation_filter |= Q(location__isnull=True) & geography_filter

    return (
        queryset.filter(relation_filter)
        if relation_filter.children
        else queryset.none()
    )


def _get_selected_srs_descendants(loc_query):
    if not loc_query:
        return SRSClusterLocation.objects.none()

    id_list = [int(pk) for pk in str(loc_query).split(",") if pk]
    if not id_list:
        return SRSClusterLocation.objects.none()

    selected = SRSClusterLocation.objects.filter(pk__in=id_list)
    descendants = selected
    for node in selected:
        descendants = descendants | node.get_descendants()
    return descendants.distinct()


def _first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _apply_date_filter(qs, start_date, end_date, date_field):
    if start_date:
        qs = qs.filter(**{f"{date_field}__gte": start_date})
    if end_date:
        qs = qs.filter(**{f"{date_field}__lte": end_date})
    return qs


def _normalize_odk_code(val):
    """Normalise an ODK choice value so '05', '5', and '5.0' all map to '5'."""
    text = str(val or "").strip()
    if re.fullmatch(r"\d+", text):
        return str(int(text))
    if re.fullmatch(r"\d+\.0+", text):
        return str(int(text.split(".", 1)[0]))
    return text


def _build_odk_label_map(field_name, form_name=None):
    """Return {normalised_code: human_readable_label} from ODKFormChoice.

    When form_name is None all forms are searched (first match wins), which is
    needed for geographic fields (province/district/constituency/ward) whose
    choices are stored under the pregnancy/pregnancy_outcome forms.
    """
    qs = ODKFormChoice.objects.filter(field_name=field_name).order_by("id")
    if form_name is not None:
        qs = qs.filter(form_name=form_name)
    lookup = {}
    for row in qs.values("value", "label"):
        key = _normalize_odk_code(row["value"])
        if key and key not in lookup:
            lookup[key] = (row["label"] or "").strip()
    return lookup


def _decode_odk_field(label_map, raw_value):
    """Resolve a coded field value to its human-readable label."""
    text = str(raw_value or "").strip()
    if not text:
        return text
    return label_map.get(_normalize_odk_code(text), text)


def _matches_geography_branches(row, branches):
    for branch in branches:
        if all(
            str(row.get(level) or "").strip().casefold() == str(name).strip().casefold()
            for level, name in branch.items()
        ):
            return True
    return False


def _export_rumor_surveillance(request, start_date, end_date, loc_query):
    queryset = CSADailyTracker.objects.exclude(desci__isnull=True).exclude(desci="")
    queryset = _apply_date_filter(queryset, start_date, end_date, "today")

    label_maps = {
        field: _build_odk_label_map(field)
        for field in ("province", "district", "constituency", "ward")
    }
    rumor_field_mapping = {level: level for level in _GEO_LEVELS}
    user_nodes = _user_location_nodes(request.user)
    user_branches = (
        _tree_geography_branches(Location, user_nodes, rumor_field_mapping)
        if user_nodes is not None
        else None
    )
    can_view_pii = request.user.can_view_pii
    selected_branches = (
        _selected_srs_geography_branches(
            [int(pk) for pk in str(loc_query).split(",") if pk],
            rumor_field_mapping,
        )
        if loc_query
        else None
    )

    def rows():
        source = queryset.values(*_RUMOR_SURVEILLANCE_COLUMNS).iterator(chunk_size=1000)
        for row in source:
            for field, label_map in label_maps.items():
                row[field] = _decode_odk_field(label_map, row.get(field))
            if user_branches is not None and not _matches_geography_branches(
                row, user_branches
            ):
                continue
            if selected_branches is not None and not _matches_geography_branches(
                row, selected_branches
            ):
                continue
            yield _redact_row(row, can_view_pii)

    audited_rows = _audit_rows(rows(), request, "rumor_surveillance", "csv")
    return stream_csv_zip_response(
        "rumor_surveillance.csv.zip",
        "rumor_surveillance_download.csv",
        _RUMOR_SURVEILLANCE_COLUMNS,
        audited_rows,
    )


def _export_non_va_dataset(request, dataset, params):
    start_date = params.get("start_date") or None
    end_date = params.get("end_date") or None
    location_ids = params.get("locations") or []
    loc_query = ",".join(str(location_id) for location_id in location_ids)

    # Rumor surveillance has coded location values that need ODK-choice decoding,
    # so it uses its own export path rather than the shared geo-filter pattern.
    if dataset == "rumor_surveillance":
        return _export_rumor_surveillance(request, start_date, end_date, loc_query)

    field_mapping = {
        "province": "province",
        "district": "district",
        "constituency": "constituency",
        "ward": "ward",
        "ea": "ea",
    }
    geo_filter = _build_geo_filter_from_srs(loc_query, field_mapping)

    _household_field_mapping = {
        "province": "province",
        "district": "district",
        "constituency": "constituency",
        "ward": "ward",
    }

    if dataset == "household":
        model = Household
        queryset = _restrict_export_queryset_to_user_locations(
            Household.objects.all(), request.user, _household_field_mapping
        )
        date_field = "today"
        filename = "household_download.csv"
    elif dataset == "pregnancy":
        model = Pregnancy
        queryset = _restrict_export_queryset_to_user_locations(
            Pregnancy.objects.all(), request.user, field_mapping
        )
        date_field = "today"
        filename = "pregnancy_download.csv"
    elif dataset == "pregnancy_outcome":
        model = PregnancyOutcome
        queryset = _restrict_export_queryset_to_user_locations(
            PregnancyOutcome.objects.all(), request.user, field_mapping
        )
        date_field = "today"
        filename = "pregnancy_outcome_download.csv"
    else:
        model = Death
        queryset = _restrict_export_queryset_to_user_locations(
            Death.objects.all(), request.user, field_mapping
        )
        date_field = "today"
        filename = "death_download.csv"

    if location_ids:
        queryset = (
            queryset.filter(geo_filter) if geo_filter.children else queryset.none()
        )
    queryset = _apply_date_filter(queryset, start_date, end_date, date_field)
    columns = _model_export_columns(model)
    can_view_pii = request.user.can_view_pii
    rows = _iter_queryset_rows(
        queryset,
        columns,
        transform=lambda row: _redact_row(row, can_view_pii),
    )
    audited_rows = _audit_rows(rows, request, dataset, "csv")
    return stream_csv_zip_response(
        f"{dataset}.csv.zip",
        filename,
        columns,
        audited_rows,
    )


def _va_column_sets(record_type):
    model_columns = _model_export_columns(VerbalAutopsy)
    full_output_columns = [
        column
        for column in model_columns
        if column not in {"location_id", "cluster_id"}
    ]
    full_output_columns.extend(
        column
        for column in [*_VA_ANNOTATION_COLUMNS, "location"]
        if column not in full_output_columns
    )

    if record_type == "abridged":
        available = set(full_output_columns)
        output_columns = [
            column for column in ABRIDGED_VA_EXPORT_COLUMNS if column in available
        ]
        selected_model_columns = [
            column for column in output_columns if column in model_columns
        ]
    else:
        output_columns = full_output_columns
        selected_model_columns = model_columns

    for foreign_key in ("location_id", "cluster_id"):
        if foreign_key not in selected_model_columns:
            selected_model_columns.append(foreign_key)

    query_columns = [
        *selected_model_columns,
        *_VA_ANNOTATION_COLUMNS,
        *_VA_QUERY_HELPER_COLUMNS,
    ]
    return output_columns, list(dict.fromkeys(query_columns))


def _filter_va_queryset(request, params):
    matching_vas = request.user.verbal_autopsies(
        date_cutoff="1901-01-01", end_date="9999-12-31"
    ).exclude(Id10023="dk")
    matching_vas = _restrict_va_export_queryset_to_user_locations(
        matching_vas,
        request.user,
    )

    va_ids = params.get("ids") or []
    if va_ids:
        matching_vas = matching_vas.filter(pk__in=va_ids)
    else:
        location_ids = params.get("locations") or []
        if location_ids:
            loc_query = ",".join(str(location_id) for location_id in location_ids)
            clusters = _get_selected_srs_descendants(loc_query)
            location_qs = map_srs_clusters_to_locations(clusters)
            va_geo_filter = _build_geo_filter_from_srs(
                loc_query,
                {
                    "province": "province",
                    "district": "district",
                    "constituency": "constituency",
                    "ward": "ward",
                    "ea": "ea",
                },
            )

            location_filter = Q()
            if clusters.exists():
                location_filter |= Q(cluster__in=clusters)
            if location_qs.exists():
                location_filter |= Q(location__in=location_qs)
            if va_geo_filter.children:
                location_filter |= va_geo_filter
            matching_vas = (
                matching_vas.filter(location_filter)
                if location_filter.children
                else matching_vas.none()
            )

        matching_vas = _apply_date_filter(
            matching_vas,
            params.get("start_date"),
            params.get("end_date"),
            "Id10023",
        )
        if params.get("causes"):
            matching_vas = matching_vas.filter(causes__cause__in=params["causes"])

    return matching_vas.annotate(
        date=F("Id10023"),
        cause=F("causes__cause"),
        cause_id=F("causes__pk"),
        cause_algorithm=F("causes__algorithm"),
        cause_created=F("causes__created"),
        loc_name=F("location__name"),
        cluster_name=F("cluster__name"),
    )


def _transform_va_row(row, can_view_pii, location_geography, cluster_geography):
    location_id = row.pop("location_id", None)
    cluster_id = row.pop("cluster_id", None)

    for level, name in location_geography.get(location_id, {}).items():
        row[level] = name
    for level, name in cluster_geography.get(cluster_id, {}).items():
        row[level] = _first_non_empty(row.get(level), name)

    for level in _GEO_LEVELS:
        row[level] = _first_non_empty(row.get(level))

    row["location"] = _first_non_empty(
        row.pop("loc_name", None), row.pop("cluster_name", None)
    )
    return _redact_row(row, can_view_pii)


def _export_va_dataset(request, params):
    record_type = params["va_record_type"]
    fmt = params["format"]
    matching_vas = _filter_va_queryset(request, params)
    output_columns, query_columns = _va_column_sets(record_type)
    row_count = matching_vas.count()

    logger.info(
        "VA export requested: user=%s rows=%s record_type=%s format=%s "
        "filters={ids:%s, locations:%s, start:%s, end:%s, causes:%s}",
        getattr(request.user, "email", None),
        row_count,
        record_type,
        fmt,
        bool(params.get("ids")),
        params.get("locations") or None,
        params.get("start_date"),
        params.get("end_date"),
        params.get("causes") or None,
    )

    location_geography = (
        _tree_geography_lookup(Location, include_self=False) if row_count else {}
    )
    cluster_geography = (
        _tree_geography_lookup(SRSClusterLocation, include_self=True)
        if row_count
        else {}
    )
    can_view_pii = request.user.can_view_pii
    rows = _iter_queryset_rows(
        matching_vas,
        query_columns,
        transform=lambda row: _transform_va_row(
            row, can_view_pii, location_geography, cluster_geography
        ),
        chunk_size=100,
    )
    audited_rows = _audit_rows(rows, request, "verbal_autopsy", fmt)

    if fmt == "json":
        return stream_json_zip_response(
            "export.json.zip",
            "va_download.json",
            row_count,
            audited_rows,
        )
    return stream_csv_zip_response(
        "export.csv.zip",
        "va_download.csv",
        output_columns,
        audited_rows,
    )


def _request_values(params, key):
    if hasattr(params, "getlist"):
        return params.getlist(key)
    value = params.get(key)
    if value is None:
        return []
    return value if isinstance(value, (list, tuple)) else [value]


def _validate_export_request(params):
    raw_format = str(params.get("format") or "csv").strip().lower().replace("/", "")
    data = {
        "dataset": str(params.get("dataset") or "verbalautopsy").strip().lower(),
        "va_record_type": str(params.get("va_record_type") or "full").strip().lower(),
        "format": raw_format,
        "start_date": params.get("start_date") or None,
        "end_date": params.get("end_date") or None,
        "locations": _request_values(params, "locations"),
        "causes": _request_values(params, "causes"),
        "ids": _request_values(params, "ids"),
    }
    form = ExportRequestForm(data)
    return form.cleaned_data if form.is_valid() else form


def _invalid_export_response(form):
    errors = {
        field: [error["message"] for error in field_errors]
        for field, field_errors in form.errors.get_json_data().items()
    }
    return JsonResponse(
        {
            "error": "The export request is invalid.",
            "errors": errors,
            "invalid_fields": list(errors),
        },
        status=400,
    )


def _dispatch_export(request, params):
    if params["dataset"] == "verbalautopsy":
        return _export_va_dataset(request, params)
    return _export_non_va_dataset(request, params["dataset"], params)


class VaApi(CustomAuthMixin, PermissionRequiredMixin, View):
    permission_required = "va_analytics.download_data"

    def post(self, request, *args, **kwargs):
        params = _validate_export_request(request.POST)
        if isinstance(params, ExportRequestForm):
            return _invalid_export_response(params)
        return _dispatch_export(request, params)


va_api_view = VaApi.as_view()


class Index(CustomAuthMixin, PermissionRequiredMixin, FormView):
    permission_required = "va_analytics.download_data"
    form_class = VADownloadForm
    template_name = "va_export/index.html"

    def form_valid(self, form):
        params = _validate_export_request(form.cleaned_data)
        if isinstance(params, ExportRequestForm):
            for field, errors in params.errors.items():
                target = field if field in form.fields else None
                for error in errors:
                    form.add_error(target, error)
            return self.form_invalid(form)
        return _dispatch_export(self.request, params)


download_view = Index.as_view()
