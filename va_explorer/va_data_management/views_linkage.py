"""VA <-> Death linkage review workflow views.

Queue (tabbed list) + detail + confirm/reject actions over ``VADeathMatch``.
Follows the ``va_data_cleanup`` conventions: ``CustomAuthMixin`` +
``PermissionRequiredMixin`` list views gated by an unmanaged permission model
(``LinkageReview``), with explicit ``has_perm`` checks on the POST actions.

Every confirm/reject is audited three ways: the who-did-what fields on the
match row (``confirmed_by``/``reviewed_by``), simple-history on the row, and a
``UserActivity`` entry. Rejecting stores a ``VADeathMatchRejection`` so the
pair is never proposed again, then immediately re-evaluates the VA so the
next-best candidate (if any) surfaces for review.
"""

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from va_explorer.users.models import UserActivity
from va_explorer.utils.mixins import CustomAuthMixin
from va_explorer.va_data_management.models import (
    Death,
    LinkageReview,
    MatchMethod,
    MatchStatus,
    VADeathMatch,
    VADeathMatchRejection,
)
from va_explorer.va_data_management.utils.matching import (
    auto_link_verbal_autopsy_to_death,
)

TABS = {
    "pending": [MatchStatus.PENDING_REVIEW],
    "ambiguous": [MatchStatus.AMBIGUOUS],
    "confirmed": [MatchStatus.MATCHED_MANUAL, MatchStatus.MATCHED_AUTO],
    "rejected": [MatchStatus.REJECTED],
}


def _match_queryset():
    return VADeathMatch.objects.select_related(
        "death", "verbal_autopsy", "vasa", "confirmed_by", "reviewed_by"
    )


def _va_label(match):
    va = match.va
    if va is None:
        return "?"
    name = " ".join(
        part
        for part in ((va.Id10017 or "").strip(), (va.Id10018 or "").strip())
        if part
    )
    return name or getattr(va, "instanceid", None) or f"#{va.pk}"


class LinkageReviewIndexView(CustomAuthMixin, PermissionRequiredMixin, ListView):
    """Tabbed review queue over VADeathMatch rows."""

    permission_required = "va_data_management.view_linkagereview"
    model = LinkageReview
    paginate_by = 20
    template_name = "va_data_management/linkage_review/index.html"

    def _active_tab(self):
        tab = self.request.GET.get("tab", "pending").strip().lower()
        return tab if tab in set(TABS) | {"duplicates"} else "pending"

    def get_queryset(self):
        tab = self._active_tab()
        qs = _match_queryset()
        if tab == "duplicates":
            # One death linked by >1 record of the same tool.
            linked = qs.filter(
                status__in=[MatchStatus.MATCHED_AUTO, MatchStatus.MATCHED_MANUAL],
                death__isnull=False,
            )
            death_pks = {}
            duplicate_pks = []
            for pk, death_pk, va_pk in linked.values_list(
                "pk", "death_id", "verbal_autopsy_id"
            ):
                key = (death_pk, va_pk is not None)  # same death, same tool
                if key in death_pks:
                    duplicate_pks.extend([death_pks[key], pk])
                else:
                    death_pks[key] = pk
            return qs.filter(pk__in=set(duplicate_pks)).order_by("death_id", "pk")
        qs = qs.filter(status__in=TABS[tab])
        method = self.request.GET.get("method", "").strip()
        if method:
            qs = qs.filter(method=method)
        district = self.request.GET.get("district", "").strip()
        if district:
            qs = qs.filter(death__district__iexact=district)
        return qs.order_by("-score", "pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tab = self._active_tab()
        context["active_tab"] = tab
        context["can_review"] = self.request.user.has_perm(
            "va_data_management.review_linkage"
        )
        counts = {}
        for name, statuses in TABS.items():
            counts[name] = VADeathMatch.objects.filter(status__in=statuses).count()
        context["tab_counts"] = counts
        context["method_filter"] = self.request.GET.get("method", "")
        context["district_filter"] = self.request.GET.get("district", "")
        context["rows"] = [
            {
                "match": match,
                "va_label": _va_label(match),
                "va_model": "VASA" if match.vasa_id else "VA",
            }
            for match in context["object_list"]
        ]
        return context


class LinkageReviewDetailView(CustomAuthMixin, PermissionRequiredMixin, DetailView):
    """One match with per-field evidence and ranked alternatives."""

    permission_required = "va_data_management.view_linkagereview"
    model = VADeathMatch
    template_name = "va_data_management/linkage_review/detail.html"
    context_object_name = "match"

    def get_queryset(self):
        return _match_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        match = context["match"]
        context["va_label"] = _va_label(match)
        context["va_model"] = "VASA" if match.vasa_id else "VA"
        context["can_review"] = self.request.user.has_perm(
            "va_data_management.review_linkage"
        )
        alternative_pks = [
            a.get("death_pk") for a in match.alternatives if a.get("death_pk")
        ]
        deaths = {d.pk: d for d in Death.objects.filter(pk__in=alternative_pks)}
        context["alternatives"] = [
            {**a, "death": deaths.get(a.get("death_pk"))} for a in match.alternatives
        ]
        return context


class _ReviewActionView(View):
    """Shared plumbing for confirm/reject POST actions."""

    def _check(self, request):
        if not request.user.has_perm("va_data_management.review_linkage"):
            raise PermissionDenied
        return get_object_or_404(_match_queryset(), pk=self.kwargs["pk"])

    def _back(self, request):
        return redirect(
            request.POST.get("next")
            or reverse("va_data_management:linkage_review_index")
        )


class ConfirmLinkView(CustomAuthMixin, _ReviewActionView):
    """Confirm the proposed link (or a chosen alternative) as manual."""

    def post(self, request, *args, **kwargs):
        match = self._check(request)
        now = timezone.now()

        chosen_pk = request.POST.get("death_pk")
        if chosen_pk and str(match.death_id) != str(chosen_pk):
            match.death = get_object_or_404(Death, pk=chosen_pk)
            match.method = MatchMethod.MANUAL
        elif match.death is None:
            messages.error(request, "No proposed death to confirm.")
            return self._back(request)
        match.death_id_snapshot = match.death.death_id or match.death_id_snapshot
        match.status = MatchStatus.MATCHED_MANUAL
        match.is_manual = True
        match.confirmed_by = request.user
        match.reviewed_by = request.user
        match.matched_at = now
        match.rejected_reason = None
        match.save()

        UserActivity.log(
            UserActivity.DATA_UPDATE,
            user=request.user,
            request=request,
            module="linkage_review",
            target_type="VADeathMatch",
            target_id=str(match.pk),
            target_display=str(match),
            details="Confirmed VA-Death link",
        )
        messages.success(request, "Link confirmed.")
        return self._back(request)


class RejectLinkView(CustomAuthMixin, _ReviewActionView):
    """Reject the proposed pair; remember it and surface the next-best."""

    def post(self, request, *args, **kwargs):
        match = self._check(request)
        reason = (request.POST.get("reason") or "").strip()

        if match.death is None:
            messages.error(request, "No proposed death to reject.")
            return self._back(request)

        pair = {"death": match.death}
        if match.vasa_id:
            pair["vasa"] = match.vasa
        else:
            pair["verbal_autopsy"] = match.verbal_autopsy
        VADeathMatchRejection.objects.get_or_create(
            defaults={"rejected_by": request.user, "reason": reason}, **pair
        )

        rejected_death = match.death
        match.status = MatchStatus.REJECTED
        match.is_manual = False
        match.reviewed_by = request.user
        match.rejected_reason = reason or None
        match.save()

        UserActivity.log(
            UserActivity.DATA_UPDATE,
            user=request.user,
            request=request,
            module="linkage_review",
            target_type="VADeathMatch",
            target_id=str(match.pk),
            target_display=str(match),
            details=f"Rejected VA-Death link (death #{rejected_death.pk})",
        )

        # Re-evaluate immediately so the next-best candidate surfaces.
        result = auto_link_verbal_autopsy_to_death(
            match.va, save=True, source="review_reject"
        )
        messages.success(
            request,
            f"Link rejected; re-evaluated -> {result.status}.",
        )
        return self._back(request)
