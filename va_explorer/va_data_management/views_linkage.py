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
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import DetailView, ListView

from va_explorer.users.models import UserActivity
from va_explorer.utils.mixins import CustomAuthMixin
from va_explorer.va_data_management.models import (
    VASA,
    Death,
    LinkageReview,
    MatchStatus,
    VADeathMatch,
    VerbalAutopsy,
)
from va_explorer.va_data_management.utils.location_access import (
    restrict_va_queryset_to_user_locations,
)
from va_explorer.va_data_management.utils.matching.review import (
    confirm_match,
    reject_match,
    unlink_match,
)

TABS = {
    "pending": [MatchStatus.PENDING_REVIEW],
    "ambiguous": [MatchStatus.AMBIGUOUS],
    "confirmed": [MatchStatus.MATCHED_MANUAL, MatchStatus.MATCHED_AUTO],
    "rejected": [],
}


def _match_queryset(user):
    verbal_autopsies = restrict_va_queryset_to_user_locations(
        VerbalAutopsy.objects.all(), user
    )
    vasas = restrict_va_queryset_to_user_locations(VASA.objects.all(), user)
    return VADeathMatch.objects.filter(
        Q(verbal_autopsy__in=verbal_autopsies) | Q(vasa__in=vasas)
    ).select_related("death", "verbal_autopsy", "vasa", "confirmed_by", "reviewed_by")


def _has_rejection(qs):
    return qs.filter(
        Q(verbal_autopsy__death_match_rejections__isnull=False)
        | Q(vasa__death_match_rejections__isnull=False)
    ).distinct()


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


def _next_best_score_display(match):
    for alternative in match.alternatives or []:
        if alternative.get("previous_link"):
            continue
        try:
            return f"{float(alternative['score']):.1f}"
        except (KeyError, TypeError, ValueError):
            continue
    return "—"


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
        qs = _match_queryset(self.request.user)
        if tab == "duplicates":
            # One death linked by >1 record of the same tool.
            linked = qs.filter(
                status__in=[MatchStatus.MATCHED_AUTO, MatchStatus.MATCHED_MANUAL],
                death__isnull=False,
            )
            death_pks = {}
            duplicate_pks = []
            for match in linked:
                if match.vasa_id:
                    tool = "vasa"
                else:
                    tool = (
                        "community_va"
                        if match.verbal_autopsy.is_community_va
                        else "facility_va"
                    )
                key = (match.death_id, tool)
                if key in death_pks:
                    duplicate_pks.extend([death_pks[key], match.pk])
                else:
                    death_pks[key] = match.pk
            return qs.filter(pk__in=set(duplicate_pks)).order_by("death_id", "pk")
        if tab == "rejected":
            qs = _has_rejection(qs)
        else:
            qs = qs.filter(status__in=TABS[tab])
        method = self.request.GET.get("method", "").strip()
        if method:
            qs = qs.filter(method=method)
        district = self.request.GET.get("district", "").strip()
        if district:
            qs = qs.filter(death__district__iexact=district)
        tool = self.request.GET.get("tool", "").strip()
        if tool == "vasa":
            qs = qs.filter(vasa__isnull=False)
        elif tool == "community_va":
            qs = qs.filter(verbal_autopsy__community_va__iexact="yes")
        elif tool == "facility_va":
            qs = qs.filter(verbal_autopsy__community_va__iexact="no")
        elif tool == "unknown":
            qs = (
                qs.filter(verbal_autopsy__isnull=False)
                .exclude(verbal_autopsy__community_va__iexact="yes")
                .exclude(verbal_autopsy__community_va__iexact="no")
            )
        min_score = self.request.GET.get("min_score", "").strip()
        max_score = self.request.GET.get("max_score", "").strip()
        try:
            if min_score:
                qs = qs.filter(score__gte=float(min_score))
            if max_score:
                qs = qs.filter(score__lte=float(max_score))
        except ValueError:
            pass
        return qs.order_by("-score", "pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tab = self._active_tab()
        context["active_tab"] = tab
        context["can_review"] = self.request.user.has_perm(
            "va_data_management.review_linkage"
        )
        counts = {}
        scoped = _match_queryset(self.request.user)
        for name, statuses in TABS.items():
            counts[name] = (
                _has_rejection(scoped).count()
                if name == "rejected"
                else scoped.filter(status__in=statuses).count()
            )
        context["tab_counts"] = counts
        context["method_filter"] = self.request.GET.get("method", "")
        context["district_filter"] = self.request.GET.get("district", "")
        for key in ("tool", "min_score", "max_score"):
            context[f"{key}_filter"] = self.request.GET.get(key, "")
        filter_params = self.request.GET.copy()
        filter_params.pop("page", None)
        context["filter_query"] = filter_params.urlencode()
        context["rows"] = [
            {
                "match": match,
                "va_label": _va_label(match),
                "next_best_score_display": _next_best_score_display(match),
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
        return _match_queryset(self.request.user)

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
        context["decisions"] = match.decisions.select_related(
            "actor", "prior_death", "new_death"
        )
        context["rejections"] = (
            match.vasa.death_match_rejections.all()
            if match.vasa_id
            else match.verbal_autopsy.death_match_rejections.all()
        )
        back_target = self.request.GET.get("next")
        if not back_target or not url_has_allowed_host_and_scheme(
            back_target,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            back_target = reverse("va_data_management:linkage_review_index")
        context["back_target"] = back_target
        return context


class _ReviewActionView(View):
    """Shared plumbing for confirm/reject POST actions."""

    def _check(self, request):
        if not request.user.has_perm("va_data_management.review_linkage"):
            raise PermissionDenied
        return get_object_or_404(_match_queryset(request.user), pk=self.kwargs["pk"])

    def _back(self, request):
        target = request.POST.get("next")
        if not target or not url_has_allowed_host_and_scheme(
            target,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            target = reverse("va_data_management:linkage_review_index")
        return redirect(target)

    def _log(self, request, match, details):
        UserActivity.log(
            UserActivity.DATA_UPDATE,
            user=request.user,
            request=request,
            module="linkage_review",
            target_type="VADeathMatch",
            target_id=str(match.pk),
            target_display=str(match),
            details=details,
        )


class ConfirmLinkView(CustomAuthMixin, _ReviewActionView):
    """Confirm the proposed link (or a chosen alternative) as manual."""

    def post(self, request, *args, **kwargs):
        match = self._check(request)
        chosen_pk = request.POST.get("death_pk")
        try:
            match = confirm_match(
                match.pk,
                chosen_pk or match.death_id,
                actor=request.user,
                reason=(request.POST.get("reason") or "").strip(),
                expected_version=request.POST.get("decision_version"),
            )
        except (ValidationError, Death.DoesNotExist) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
            return self._back(request)
        self._log(request, match, "Confirmed VA-Death link")
        messages.success(request, "Link confirmed.")
        return self._back(request)


class RejectLinkView(CustomAuthMixin, _ReviewActionView):
    """Reject the proposed pair; remember it and surface the next-best."""

    def post(self, request, *args, **kwargs):
        match = self._check(request)
        reason = (request.POST.get("reason") or "").strip()
        rejected_death_id = match.death_id
        try:
            match, result = reject_match(
                match.pk,
                actor=request.user,
                reason=reason,
                expected_version=request.POST.get("decision_version"),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return self._back(request)
        self._log(
            request, match, f"Rejected VA-Death link (death #{rejected_death_id})"
        )
        messages.success(
            request,
            f"Link rejected; re-evaluated -> {result.status}.",
        )
        return self._back(request)


class UnlinkView(CustomAuthMixin, _ReviewActionView):
    """Remove the current link while retaining a permanent decision event."""

    def post(self, request, *args, **kwargs):
        match = self._check(request)
        reason = (request.POST.get("reason") or "").strip()
        try:
            match = unlink_match(
                match.pk,
                actor=request.user,
                reason=reason,
                expected_version=request.POST.get("decision_version"),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return self._back(request)
        self._log(request, match, "Unlinked VA-Death record")
        messages.success(request, "Link removed.")
        return self._back(request)
