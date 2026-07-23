from collections import defaultdict

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse
from django.views.generic import ListView, View

from ..utils.file_io import download_list_as_csv, download_queryset_as_csv
from ..utils.mixins import CustomAuthMixin
from ..va_data_management.constants import REDACTED_STRING
from ..va_data_management.models import (
    CauseCodingIssue,
    CauseOfDeath,
    Death,
    Household,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
    questions_to_autodetect_duplicates,
)
from ..va_data_management.models.data_quality import (
    DataQualityIssue,
    DataQualityIssueMember,
)
from ..va_data_management.utils.date_parsing import parse_date
from .models import DataCleanup

User = get_user_model()
DQ_PER_PAGE = 25
VA_SUBTAB_PER_PAGE = 10
VA_SUBTABS = {"duplicates", "coding-issues", "indeterminate", "errors", "missing-gps"}


def _paginate(request, queryset, page_param, per_page=VA_SUBTAB_PER_PAGE):
    """Return the requested Django ``Page`` for ``queryset`` using ``page_param``."""
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get(page_param))


def _dq_rows_for_model(request, model, build_row, only_fields):
    """Return one bounded page of records with open data-quality issues."""
    content_type = ContentType.objects.get_for_model(model)
    issues = DataQualityIssue.objects.filter(
        target_model=content_type,
        status=DataQualityIssue.OPEN,
    ).order_by()

    member_groups = (
        DataQualityIssueMember.objects.filter(
            content_type=content_type,
            issue__target_model=content_type,
            issue__status=DataQualityIssue.OPEN,
        )
        .values_list("object_id", flat=True)
        .order_by("-object_id")
        .distinct()
    )
    page = _paginate(request, member_groups, "dq_page", per_page=DQ_PER_PAGE)
    object_ids = list(page.object_list)

    labels_by_object = defaultdict(set)
    if object_ids:
        members = (
            DataQualityIssueMember.objects.filter(
                content_type=content_type,
                object_id__in=object_ids,
                issue__target_model=content_type,
                issue__status=DataQualityIssue.OPEN,
            )
            .select_related("issue")
            .only("object_id", "issue__issue_type", "issue__details")
        )
        for member in members:
            labels_by_object[member.object_id].add(_issue_label(member.issue))

    records = model.objects.filter(id__in=object_ids).only(*only_fields).in_bulk()
    rows = [
        build_row(records[object_id], sorted(labels_by_object[object_id]))
        for object_id in object_ids
        if object_id in records
    ]
    return rows, issues.count(), page


def _with_va_table_data(queryset):
    """Limit wide VA rows and related objects to fields rendered by cleanup tables."""
    return (
        queryset.select_related("location")
        .prefetch_related(
            Prefetch(
                "causes",
                queryset=CauseOfDeath.objects.only(
                    "verbalautopsy_id", "cause"
                ).order_by("id"),
            ),
            Prefetch(
                "coding_issues",
                queryset=CauseCodingIssue.objects.only("verbalautopsy_id", "severity"),
            ),
        )
        .only(
            "id",
            "Id10010",
            "Id10012",
            "Id10017",
            "Id10018",
            "Id10023",
            "location_id",
            "location__name",
        )
    )


def _household_row(record, issues):
    return {
        "id": record.id,
        "key": record.key,
        "hhn": record.hhn,
        "province": record.province or "-",
        "district": record.district or "-",
        "ward": record.ward or "-",
        "ea": record.ea or "-",
        "issues": issues,
    }


def _pregnancy_row(record, issues):
    return {
        "id": record.id,
        "record_name": record.PE_06 or "-",
        "household_ref": record.PE_04 or "-",
        "enumerator": record.enumerator or "-",
        "province": record.province or "-",
        "district": record.district or "-",
        "ward": record.ward or "-",
        "issues": issues,
    }


def _pregnancy_outcome_row(record, issues):
    return {
        "id": record.id,
        "record_name": record.PO_04 or "-",
        "household_ref": record.PO_02 or "-",
        "outcome_date": record.PO_41 or "-",
        "outcome_type": record.PO_46 or "-",
        "enumerator": record.enumerator or "-",
        "province": record.province or "-",
        "district": record.district or "-",
        "ward": record.ward or "-",
        "issues": issues,
    }


def _death_row(record, issues):
    return {
        "id": record.id,
        "record_name": record.DE_03 or "-",
        "household_ref": record.DE_01 or "-",
        "death_date": record.DE_06 or "-",
        "enumerator": record.enumerator or "-",
        "province": record.province or "-",
        "district": record.district or "-",
        "ward": record.ward or "-",
        "issues": issues,
    }


def _first_cause(va):
    """Return the first coded cause string using the prefetched causes cache."""
    causes = va.causes.all()
    return causes[0].cause if causes else "Not Coded"


def _issue_severity_counts(va):
    """Count warnings and errors in a single pass over the prefetched coding issues."""
    warnings = 0
    errors = 0
    for issue in va.coding_issues.all():
        if issue.severity == "warning":
            warnings += 1
        elif issue.severity == "error":
            errors += 1
    return warnings, errors


def _issue_label(issue):
    subtype = str((issue.details or {}).get("subtype") or "").strip()
    if subtype:
        return f"{issue.get_issue_type_display()} ({subtype.replace('_', ' ')})"
    return issue.get_issue_type_display()


def _get_context_for_va_table(va_list, user):
    context = []
    for va in va_list:
        warnings, errors = _issue_severity_counts(va)
        context.append(
            {
                "id": va.id,
                "deceased": f"{va.Id10017} {va.Id10018}",
                "interviewer": va.Id10010,
                "interviewed": (
                    parse_date(va.Id10012) if (va.Id10012 != "dk") else "Unknown"
                ),
                "dod": parse_date(va.Id10023) if (va.Id10023 != "dk") else "Unknown",
                "facility": va.location.name if va.location else "Not Provided",
                "cause": _first_cause(va),
                "warnings": warnings,
                "errors": errors,
            }
        )
    for item in context:
        if not user.can_view_pii:
            item["deceased"] = REDACTED_STRING
    return context


class DataCleanupIndexView(CustomAuthMixin, PermissionRequiredMixin, ListView):
    permission_required = "va_data_cleanup.view_datacleanup"
    model = DataCleanup
    paginate_by = 10
    template_name = "va_data_cleanup/index.html"

    def get_active_tab(self):
        active_tab = self.request.GET.get("tab", "verbal-autopsy").strip().lower()
        allowed_tabs = {
            "household",
            "pregnancy",
            "pregnancy-outcome",
            "death",
            "verbal-autopsy",
        }
        return active_tab if active_tab in allowed_tabs else "verbal-autopsy"

    def get_active_va_subtab(self):
        active_subtab = self.request.GET.get("va_subtab", "duplicates").strip().lower()
        return active_subtab if active_subtab in VA_SUBTABS else "duplicates"

    def get_queryset(self):
        if not (
            self.get_active_tab() == "verbal-autopsy"
            and self.get_active_va_subtab() == "duplicates"
        ):
            return VerbalAutopsy.objects.none()

        return _with_va_table_data(
            self.request.user.verbal_autopsies().filter(duplicate=True).order_by("id")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_tab = self.get_active_tab()
        active_va_subtab = self.get_active_va_subtab()
        context["active_cleanup_tab"] = active_tab
        context["active_va_subtab"] = active_va_subtab
        context["va_data_cleanup"] = True
        context.update(
            {
                "total_duplicate_records": 0,
                "va_duplicate_rows": [],
                "va_coding_issue_count": 0,
                "va_indeterminate_count": 0,
                "va_error_count": 0,
                "va_missing_gps_count": 0,
                "va_coding_issue_rows": [],
                "va_indeterminate_cod_rows": [],
                "va_error_rows": [],
                "va_missing_gps_rows": [],
                "household_issue_rows": [],
                "pregnancy_issue_rows": [],
                "pregnancy_outcome_issue_rows": [],
                "death_issue_rows": [],
            }
        )

        if active_tab != "verbal-autopsy":
            self._add_data_quality_context(context, active_tab)
            return context

        user_vas = self.request.user.verbal_autopsies()
        if active_va_subtab == "duplicates":
            context["total_duplicate_records"] = context["paginator"].count
            va_duplicate_rows = _get_context_for_va_table(
                context["object_list"], self.request.user
            )
            context["object_list"] = va_duplicate_rows
            context["va_duplicate_rows"] = va_duplicate_rows
        else:
            context["total_duplicate_records"] = user_vas.filter(duplicate=True).count()

        uncoded_vas = user_vas.filter(causes__isnull=True).order_by("id").distinct()
        indeterminate_vas = (
            user_vas.filter(causes__cause="Indeterminate").order_by("id").distinct()
        )
        error_vas = (
            user_vas.filter(coding_issues__severity="error").order_by("id").distinct()
        )
        # "Missing GPS" = geopoint never captured (empty/whitespace), matching the
        # download_va_with_cod_csv "missing" classification (not "invalid").
        missing_gps_vas = user_vas.filter(
            Q(geopoint__isnull=True) | Q(geopoint__regex=r"^\s*$")
        ).order_by("id")

        # Counts feed the sub-tab badges (cheap; no row building).
        context["va_coding_issue_count"] = uncoded_vas.count()
        context["va_indeterminate_count"] = indeterminate_vas.count()
        context["va_error_count"] = error_vas.count()
        context["va_missing_gps_count"] = missing_gps_vas.count()

        subtab_sources = {
            "coding-issues": (uncoded_vas, "ci_page", "va_coding_issue_rows"),
            "indeterminate": (
                indeterminate_vas,
                "ind_page",
                "va_indeterminate_cod_rows",
            ),
            "errors": (error_vas, "err_page", "va_error_rows"),
            "missing-gps": (missing_gps_vas, "gps_page", "va_missing_gps_rows"),
        }
        if active_va_subtab in subtab_sources:
            queryset, page_param, rows_key = subtab_sources[active_va_subtab]
            page = _paginate(
                self.request,
                _with_va_table_data(queryset),
                page_param,
            )
            context[rows_key] = _get_context_for_va_table(
                page.object_list, self.request.user
            )
            context["va_subtab_page"] = page
            context["va_subtab_page_param"] = page_param

        return context

    def _add_data_quality_context(self, context, active_tab):
        tab_config = {
            "household": (
                Household,
                _household_row,
                ("id", "key", "hhn", "province", "district", "ward", "ea"),
                "household_issue_rows",
                "dq_issue_total",
            ),
            "pregnancy": (
                Pregnancy,
                _pregnancy_row,
                (
                    "id",
                    "PE_04",
                    "PE_06",
                    "enumerator",
                    "province",
                    "district",
                    "ward",
                ),
                "pregnancy_issue_rows",
                "dq_pregnancy_total",
            ),
            "pregnancy-outcome": (
                PregnancyOutcome,
                _pregnancy_outcome_row,
                (
                    "id",
                    "PO_02",
                    "PO_04",
                    "PO_41",
                    "PO_46",
                    "enumerator",
                    "province",
                    "district",
                    "ward",
                ),
                "pregnancy_outcome_issue_rows",
                "dq_pregnancy_outcome_total",
            ),
            "death": (
                Death,
                _death_row,
                (
                    "id",
                    "DE_01",
                    "DE_03",
                    "DE_06",
                    "enumerator",
                    "province",
                    "district",
                    "ward",
                ),
                "death_issue_rows",
                "dq_death_total",
            ),
        }
        model, row_builder, fields, rows_key, total_key = tab_config[active_tab]
        rows, total, page = _dq_rows_for_model(self.request, model, row_builder, fields)
        context[rows_key] = rows
        context[total_key] = total
        context["dq_page"] = page


data_cleanup_index_view = DataCleanupIndexView.as_view()


class DownloadIndividual(View):
    def get(self, request, **kwargs):
        pk = kwargs.pop("pk", None)

        if not pk or not request.user.has_perm("va_data_cleanup.download"):
            raise PermissionDenied

        if pk:
            try:
                va = VerbalAutopsy.objects.get(pk=pk)
                # Check that the VA passed in is indeed a duplicate and is a VA
                # that the user can access. Guards against a user manually
                # passing in an arbitrary VA ID to va_data_cleanup/download/:id
                if (
                    not self.request.user.verbal_autopsies()
                    .filter(id=va.id, duplicate=True)
                    .exists()
                ):
                    raise PermissionDenied

                query_set = (
                    self.request.user.verbal_autopsies()
                    .filter(unique_va_identifier=va.unique_va_identifier)
                    .order_by("created")
                )

                response = download_queryset_as_csv(
                    query_set, "duplicate_vas_matching_individual", "data_cleanup/"
                )

                return HttpResponse(response, content_type="text/csv")
            # Encountered if user manually passes in a pk to URL that does not exist or
            # User manually passes in the pk of a soft-deleted VA
            except VerbalAutopsy.DoesNotExist as err:
                raise Http404("This Verbal Autopsy does not exist.") from err


download = DownloadIndividual.as_view()


class DownloadAll(View):
    def get(self, request, **kwargs):
        if not request.user.has_perm("va_data_cleanup.bulk_download"):
            raise PermissionDenied

        query_set = (
            self.request.user.verbal_autopsies()
            .filter(duplicate=True)
            .order_by("unique_va_identifier")
        )

        data = download_queryset_as_csv(query_set, "all_duplicates", "data_cleanup/")

        return HttpResponse(data, content_type="text/csv")


download_all = DownloadAll.as_view()


# Download the questions used to autodetect duplicate VAs as a csv
class DownloadQuestions(View):
    def get(self, request, **kwargs):
        if not request.user.has_perm("va_data_cleanup.view_datacleanup"):
            raise PermissionDenied

        data = download_list_as_csv(
            questions_to_autodetect_duplicates(),
            "questions_to_autodetect_duplicates",
            "data_cleanup/",
        )
        return HttpResponse(data, content_type="text/csv")


download_questions = DownloadQuestions.as_view()
