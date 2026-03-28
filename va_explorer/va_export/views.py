import json
import zipfile
from urllib.parse import urlencode

import pandas as pd
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView, View
from django.views.generic.edit import FormView

from va_explorer.utils.mixins import CustomAuthMixin
from va_explorer.va_data_management.constants import PII_FIELDS, REDACTED_STRING
from va_explorer.users.utils.location_mapping import map_srs_clusters_to_locations
from va_explorer.va_data_management.models import (
    Death,
    Household,
    Location,
    Pregnancy,
    PregnancyOutcome,
    SRSClusterLocation,
)
from va_explorer.va_export.forms import VADownloadForm


EXPORT_DATASETS = {
    "verbalautopsy",
    "household",
    "pregnancy",
    "pregnancy_outcome",
    "death",
}


def _normalize_export_format(raw_value, dataset):
    fmt = str(raw_value or "csv").lower().replace("/", "")
    if dataset != "verbalautopsy":
        return "csv"
    return fmt if fmt in {"csv", "json"} else "csv"


def _build_zip_response(filename, body):
    response = HttpResponse(content_type="application/zip")
    response["Content-Disposition"] = f"attachment; filename={filename}"
    response.status_code = 200
    archive = zipfile.ZipFile(response, "w", zipfile.ZIP_DEFLATED)
    archive.writestr(*body)
    archive.close()
    return response


def _build_geo_filter_from_srs(loc_query, field_mapping):
    if not loc_query:
        return Q()
    id_list = [int(pk) for pk in str(loc_query).split(",") if pk]
    if not id_list:
        return Q()
    selected = SRSClusterLocation.objects.filter(pk__in=id_list)
    descendants = selected
    for node in selected:
        descendants = descendants | node.get_descendants()
    descendants = descendants.distinct()

    level_names = {level: set() for level in field_mapping.keys()}
    for node in descendants:
        level = str(node.location_type or "").lower()
        if level in level_names and node.name:
            level_names[level].add(str(node.name).strip())

    geo_filter = Q()
    for level, names in level_names.items():
        if not names:
            continue
        field_name = field_mapping[level]
        geo_filter |= Q(**{f"{field_name}__in": list(names)})
    return geo_filter


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


def _export_non_va_dataset(request, dataset, params):
    start_date = params.get("start_date") or None
    end_date = params.get("end_date") or None
    loc_query = params.get("locations") or None

    field_mapping = {
        "province": "province",
        "district": "district",
        "constituency": "constituency",
        "ward": "ward",
        "ea": "ea",
    }
    geo_filter = _build_geo_filter_from_srs(loc_query, field_mapping)

    if dataset == "household":
        queryset = Household.objects.all()
        date_field = "today"
        filename = "household_download.csv"
    elif dataset == "pregnancy":
        queryset = Pregnancy.objects.all()
        date_field = "today"
        filename = "pregnancy_download.csv"
    elif dataset == "pregnancy_outcome":
        queryset = PregnancyOutcome.objects.all()
        date_field = "today"
        filename = "pregnancy_outcome_download.csv"
    else:
        queryset = Death.objects.all()
        date_field = "today"
        filename = "death_download.csv"

    if getattr(geo_filter, "children", None):
        queryset = queryset.filter(geo_filter)
    queryset = _apply_date_filter(queryset, start_date, end_date, date_field)

    dataset_df = pd.DataFrame.from_records(queryset.values())
    if "index" in dataset_df.columns:
        dataset_df = dataset_df.drop(columns=["index"])

    if not request.user.can_view_pii:
        for field in PII_FIELDS:
            if field in dataset_df.columns:
                dataset_df[field] = REDACTED_STRING

    return _build_zip_response(
        filename=f"{dataset}.csv.zip",
        body=(filename, dataset_df.to_csv(index=False)),
    )


@method_decorator(csrf_exempt, name="dispatch")
class VaApi(CustomAuthMixin, View):
    permission_required = "va_analytics.download_data"

    def post(self, request, *args, **kwargs):
        # params = super(VaApi, self).get(self, request, *args, **kwargs)
        # get all params
        params = request.POST

        # for nullity checks
        empty_values = (None, "None", "", [])
        dataset = str(params.get("dataset") or "verbalautopsy").strip().lower()
        if dataset not in EXPORT_DATASETS:
            dataset = "verbalautopsy"

        if dataset != "verbalautopsy":
            return _export_non_va_dataset(request, dataset, params)

        # NOTE: using same filters as dashboard - exclude vas w/ null locations,
        # unknown death dates, or unknown CODs
        matching_vas = (
            request.user.verbal_autopsies()
            .exclude(Id10023="dk")
            .select_related("location")
            .annotate(
                date=F("Id10023"),
                cause=F("causes__cause"),
                loc_id=F("location__id"),
                loc_name=F("location__name"),
                cluster_id=F("cluster__id"),
                cluster_name=F("cluster__name"),
            )
        )

        # =========ID FILTER LOGIC=========================#
        # if list of VA IDs provided, only download VAs with matching IDs
        # (bypassing all other logic).
        va_ids = params.get("ids", None)
        if va_ids not in empty_values:
            # if comma-separated string, split into list
            if isinstance(va_ids, str):
                # otherwise, just single ID string - wrap in list
                va_ids = va_ids.split(",") if "," in va_ids else [va_ids]
            # merge in cause information before returning
            matching_vas = (
                matching_vas.filter(pk__in=va_ids)
                .select_related("causes")
                .annotate(cause=F("causes__cause"), cause_id=F("causes__pk"))
                .values()
            )
        # otherwise, proceed to check for other filters
        else:
            # =========LOCATION FILTER LOGIC===================#
            # if location query, filter down VAs within chosen location's jurisdiction
            loc_query = params.get("locations", None)
            if loc_query:
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
                if getattr(va_geo_filter, "children", None):
                    location_filter |= va_geo_filter

                if location_filter.children:
                    matching_vas = matching_vas.filter(location_filter)
                else:
                    matching_vas = matching_vas.none()

            # =========DATE FILTER LOGIC===================#
            # if start/end dates specified, filter to only VAs within time range
            start_date = params.get("start_date", None)
            end_date = params.get("end_date", None)

            if start_date not in empty_values:
                start_date = (
                    start_date[0] if isinstance(start_date, list) else start_date
                )
                matching_vas = matching_vas.filter(Id10023__gte=start_date)

            if end_date not in empty_values:
                end_date = end_date[0] if isinstance(end_date, list) else end_date
                matching_vas = matching_vas.filter(Id10023__lte=end_date)

            # get causes for matching vas and convert to list of records
            matching_vas = (
                matching_vas.select_related("causes")
                .annotate(cause=F("causes__cause"), cause_id=F("causes__pk"))
                .values()
            )

            # =========COD FILTER LOGIC===================#
            cod_query = params.get("causes", None)
            if cod_query not in empty_values:
                # get all valid cod ids
                # #TODO - make this work with if cod names provided
                match_list = cod_query.split(",")
                # filter VA queryset down to just those with matching location_ids
                matching_vas = matching_vas.filter(cause__in=match_list)

        # =========DATA CLEANING (if any matching VAs)========#
        va_df = pd.DataFrame()

        if matching_vas.count() > 0:
            # Build a location ancestors lookup and add location information at
            # all levels to all vas
            location_ancestors = {
                location.id: location.get_ancestors()
                for location in Location.objects.filter(location_type="facility")
            }
            cluster_ancestors = {
                cluster.id: list(cluster.get_ancestors()) + [cluster]
                for cluster in SRSClusterLocation.objects.filter(
                    location_type__in=[
                        "province",
                        "district",
                        "constituency",
                        "ward",
                        "ea",
                    ]
                )
            }

            # extract COD and location-based fields for each va object and
            # convert to dicts
            for va in matching_vas:
                if va.get("loc_id") in location_ancestors:
                    for ancestor in location_ancestors[va["loc_id"]]:
                        va[ancestor.location_type] = ancestor.name

                if va.get("cluster_id") in cluster_ancestors:
                    for ancestor in cluster_ancestors[va["cluster_id"]]:
                        loc_type = str(ancestor.location_type or "").lower()
                        if loc_type in {"province", "district", "constituency", "ward", "ea"}:
                            va[loc_type] = _first_non_empty(
                                va.get(loc_type),
                                ancestor.name,
                            )

                va["province"] = _first_non_empty(va.get("province"))
                va["district"] = _first_non_empty(va.get("district"))
                va["constituency"] = _first_non_empty(va.get("constituency"))
                va["ward"] = _first_non_empty(va.get("ward"))
                va["ea"] = _first_non_empty(va.get("ea"))

                # Clean up location fields.
                va["location"] = _first_non_empty(va.get("loc_name"), va.get("cluster_name"))
                del (va["loc_name"], va["loc_id"], va["cluster_name"], va["cluster_id"])

            # convert results to dataframe
            va_df = pd.DataFrame.from_records(matching_vas)

            if "index" in va_df.columns:
                va_df = va_df.drop(columns=["index"])

            # If user cannot view PII, redact all PII fields:
            if not request.user.can_view_pii:
                for field in PII_FIELDS:
                    if field in va_df.columns:
                        va_df[field] = REDACTED_STRING

        # =========DATA FORMAT LOGIC===================#
        # convert VAs to proper format. Currently supports .csv (default) and .json
        fmt = _normalize_export_format(params.get("format", "csv"), dataset)

        # download only for csv
        if fmt.endswith("csv"):
            response = _build_zip_response(
                filename="export.csv.zip",
                body=("va_download.csv", va_df.to_csv(index=False)),
            )
        # download for json
        elif fmt.endswith("json"):
            response = _build_zip_response(
                filename="export.json.zip",
                body=(
                    "va_download.json",
                    json.dumps(
                        {
                            "count": va_df.shape[0],
                            "records": va_df.to_json(orient="records"),
                        }
                    ),
                ),
            )
        else:
            response = HttpResponse()
        return response


va_api_view = VaApi.as_view()


class Index(CustomAuthMixin, PermissionRequiredMixin, TemplateView, FormView):
    permission_required = "va_analytics.download_data"
    form_class = VADownloadForm
    template_name = "va_export/index.html"
    success_url = "verbalautopsy"

    def form_valid(self, form):
        form_data = form.cleaned_data
        api_url = reverse("va_export:va_api") + "?" + urlencode(form_data)
        return redirect(api_url)


download_view = Index.as_view()
