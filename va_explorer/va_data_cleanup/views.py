from collections import defaultdict

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db.models import CharField
from django.db.models import Value as V
from django.db.models.functions import Concat
from django.http import Http404, HttpResponse
from django.views.generic import ListView, View

from ..utils.file_io import download_list_as_csv, download_queryset_as_csv
from ..utils.mixins import CustomAuthMixin
from ..va_data_management.constants import REDACTED_STRING
from ..va_data_management.models import (
    Death,
    Household,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
    questions_to_autodetect_duplicates,
)
from ..va_data_management.models.data_quality import DataQualityIssue
from ..va_data_management.utils.date_parsing import parse_date
from .models import DataCleanup

User = get_user_model()
MAX_VA_TABLE_ROWS = 5


def _dq_rows_for_model(model, build_row, only_fields):
    """Build display rows for a model's own open DataQualityIssues.

    Returns ``(rows, issue_group_count)``. Each record is annotated with the sorted
    set of human-readable issue labels affecting it (via DataQualityIssueMember).
    """
    content_type = ContentType.objects.get_for_model(model)
    issues = (
        DataQualityIssue.objects.filter(
            target_model=content_type,
            status=DataQualityIssue.OPEN,
        )
        .prefetch_related("members")
        .order_by("-detected_at", "-id")
    )

    labels_by_object = defaultdict(set)
    for issue in issues:
        label = _issue_label(issue)
        for member in issue.members.all():
            labels_by_object[member.object_id].add(label)

    issue_count = len(issues)
    if not labels_by_object:
        return [], issue_count

    records = model.objects.filter(id__in=labels_by_object.keys()).only(*only_fields)
    rows = [
        build_row(record, sorted(labels_by_object[record.id]))
        for record in records
        if record.id in labels_by_object
    ]
    rows.sort(key=lambda row: row["id"], reverse=True)
    return rows, issue_count


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

    def get_queryset(self):
        queryset = (
            self.request.user.verbal_autopsies()
            .prefetch_related("location", "causes", "coding_issues")
            .annotate(
                deceased=Concat("Id10017", V(" "), "Id10018", output_field=CharField())
            )
            .order_by("id")
            .filter(duplicate=True)
        )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user_vas = self.request.user.verbal_autopsies()

        context["total_duplicate_records"] = user_vas.filter(duplicate=True).count()
        context["va_data_cleanup"] = True

        va_duplicate_rows = []
        for va in context["object_list"]:
            warnings, errors = _issue_severity_counts(va)
            va_duplicate_rows.append(
                {
                    "id": va.id,
                    "interviewer": va.Id10010,
                    "interviewed": parse_date(va.Id10012),
                    "dod": (
                        parse_date(va.Id10023) if (va.Id10023 != "dk") else "Unknown"
                    ),
                    "facility": va.location.name if va.location else "Not Provided",
                    "deceased": va.deceased,
                    "cause": _first_cause(va),
                    "warnings": warnings,
                    "errors": errors,
                }
            )
        context["object_list"] = va_duplicate_rows
        context["va_duplicate_rows"] = va_duplicate_rows
        # Keep parity with the former Home VA Statistics tables.
        uncoded_vas = (
            user_vas.filter(causes__isnull=True)
            .prefetch_related("causes", "coding_issues", "location")
            .distinct()
        )
        indeterminate_vas = (
            user_vas.filter(causes__cause="Indeterminate")
            .prefetch_related("causes", "coding_issues", "location")
            .distinct()
        )
        context["va_coding_issue_rows"] = _get_context_for_va_table(
            uncoded_vas[:MAX_VA_TABLE_ROWS], self.request.user
        )
        context["va_indeterminate_cod_rows"] = _get_context_for_va_table(
            indeterminate_vas[:MAX_VA_TABLE_ROWS], self.request.user
        )
        context["additional_issues"] = max(uncoded_vas.count() - MAX_VA_TABLE_ROWS, 0)
        context["additional_indeterminate_cods"] = max(
            indeterminate_vas.count() - MAX_VA_TABLE_ROWS,
            0,
        )
        active_tab = self.request.GET.get("tab", "verbal-autopsy").strip().lower()
        allowed_tabs = {
            "household",
            "pregnancy",
            "pregnancy-outcome",
            "death",
            "verbal-autopsy",
        }
        if active_tab not in allowed_tabs:
            active_tab = "verbal-autopsy"
        context["active_cleanup_tab"] = active_tab

        # Each tab shows the data quality issues detected on its own records,
        # generated automatically on load (or via the dq_* management commands).
        household_rows, household_total = _dq_rows_for_model(
            Household,
            _household_row,
            ("id", "key", "hhn", "province", "district", "ward", "ea"),
        )
        context["household_issue_rows"] = household_rows
        context["dq_issue_total"] = household_total

        pregnancy_rows, pregnancy_total = _dq_rows_for_model(
            Pregnancy,
            _pregnancy_row,
            ("id", "PE_04", "PE_06", "enumerator", "province", "district", "ward"),
        )
        context["pregnancy_issue_rows"] = pregnancy_rows
        context["dq_pregnancy_total"] = pregnancy_total

        outcome_rows, outcome_total = _dq_rows_for_model(
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
        )
        context["pregnancy_outcome_issue_rows"] = outcome_rows
        context["dq_pregnancy_outcome_total"] = outcome_total

        death_rows, death_total = _dq_rows_for_model(
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
        )
        context["death_issue_rows"] = death_rows
        context["dq_death_total"] = death_total

        return context


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
