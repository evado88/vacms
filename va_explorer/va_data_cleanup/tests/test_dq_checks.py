import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from va_explorer.tests.factories import (
    CauseCodingIssueFactory,
    CauseOfDeathFactory,
    GroupFactory,
    UserFactory,
    VerbalAutopsyFactory,
)
from va_explorer.va_data_cleanup import views as cleanup_views
from va_explorer.va_data_management.models import (
    Death,
    Household,
    Pregnancy,
    PregnancyOutcome,
)
from va_explorer.va_data_management.models.data_quality import (
    DataQualityIssue,
    DataQualityIssueMember,
)
from va_explorer.va_data_management.utils.dq_specs import (
    DQ_SPECS,
    run_dq_for_form,
    run_dq_for_model,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Builders (bulk_create only: Death.objects.create() queues a Celery task to an
# unreachable broker and hangs the test suite).
# --------------------------------------------------------------------------- #


def _death(key, **overrides):
    defaults = {
        "key": key,
        "province": "Lusaka",
        "district": "Lusaka",
        "constituency": "Kabwata",
        "ward": "Ward 1",
        "ea": "EA-1",
        "enumerator": "Enum A",
        "consent": "yes",
        "DE_01": "HH-1",
        "DE_03": "John Doe",
        "DE_05": "Female",
        "DE_06": "2026-01-01",
        "start": "2026-01-02 09:00:00",
        "today": "2026-01-02",
        "submissiondate": "2026-01-02 09:30:00",
    }
    defaults.update(overrides)
    return Death(**defaults)


def _household(key, **overrides):
    defaults = {
        "key": key,
        "province": "Lusaka",
        "district": "Lusaka",
        "constituency": "Kabwata",
        "ward": "Ward 1",
        "ea": "EA-1",
        "hun": "HUN-1",
        "hhn": "HHN-1",
        "start": "2026-01-02 09:00:00",
        "end": "2026-01-02 09:30:00",
        "enumerator": "Enum A",
        "respondent": "Resp A",
        "household": "yes",
        "consent": "yes",
        "HH_01": "Head",
        "HH_02": "5",
    }
    defaults.update(overrides)
    return Household(**defaults)


def _bulk(model, objs):
    model.objects.bulk_create(objs)


def _issues_for(model, issue_type=None):
    ct = ContentType.objects.get_for_model(model)
    qs = DataQualityIssue.objects.filter(target_model=ct)
    if issue_type:
        qs = qs.filter(issue_type=issue_type)
    return qs


# --------------------------------------------------------------------------- #
# Death checks
# --------------------------------------------------------------------------- #


def test_death_duplicate_detection_groups_members():
    _bulk(Death, [_death("d1"), _death("d2"), _death("d3", DE_03="Other Person")])
    run_dq_for_model(DQ_SPECS[Death], write_reports=False)

    dup = _issues_for(Death, DataQualityIssue.DUPLICATE).get()
    assert dup.details["subtype"] == "deceased_household_dod"
    assert dup.members.count() == 2


def test_death_completeness_flags_missing_required_field():
    _bulk(Death, [_death("d1", DE_06="")])
    run_dq_for_model(DQ_SPECS[Death], write_reports=False)

    issue = _issues_for(Death, DataQualityIssue.INCOMPLETE).get()
    assert "DE_06" in issue.details["missing_fields"]


def test_death_consent_flags_non_affirmative():
    _bulk(Death, [_death("d1", consent="no")])
    run_dq_for_model(DQ_SPECS[Death], write_reports=False)

    assert _issues_for(Death, DataQualityIssue.CONSENT).exists()


def test_death_validity_flags_male_maternal_flag_and_future_dod():
    _bulk(Death, [_death("d1", DE_05="Male", DE_16="yes", DE_06="2999-01-01")])
    run_dq_for_model(DQ_SPECS[Death], write_reports=False)

    issue = _issues_for(Death, DataQualityIssue.VALIDITY).get()
    violations = issue.details["violations"]
    assert "died_pregnant_but_sex_recorded_male" in violations
    assert "date_of_death_in_future" in violations


def test_death_validity_flags_dob_after_dod():
    _bulk(Death, [_death("d1", DE_04="2026-06-01", DE_06="2026-01-01")])
    run_dq_for_model(DQ_SPECS[Death], write_reports=False)

    issue = _issues_for(Death, DataQualityIssue.VALIDITY).get()
    assert "date_of_birth_after_date_of_death" in issue.details["violations"]


def test_rerun_resolves_fixed_issue():
    _bulk(Death, [_death("d1", enumerator="")])
    run_dq_for_model(DQ_SPECS[Death], write_reports=False)
    issue = _issues_for(Death, DataQualityIssue.INCOMPLETE).get()
    assert issue.status == DataQualityIssue.OPEN

    Death.objects.filter(key="d1").update(enumerator="Enum A")
    run_dq_for_model(DQ_SPECS[Death], write_reports=False)

    issue.refresh_from_db()
    assert issue.status == DataQualityIssue.RESOLVED


# --------------------------------------------------------------------------- #
# Household checks (verifies the refactor preserved the original logic)
# --------------------------------------------------------------------------- #


def test_household_exact_duplicate_detection():
    _bulk(Household, [_household("h1"), _household("h2")])
    run_dq_for_model(DQ_SPECS[Household], write_reports=False)

    dup = _issues_for(Household, DataQualityIssue.DUPLICATE).get()
    assert dup.details["subtype"] == "exact_fields"
    assert dup.members.count() == 2


def test_household_completeness_flags_missing_hh01_when_household_yes():
    _bulk(Household, [_household("h1", HH_01="")])
    run_dq_for_model(DQ_SPECS[Household], write_reports=False)

    issue = _issues_for(Household, DataQualityIssue.INCOMPLETE).get()
    assert issue.details["subtype"] == "minimum_viable_record"


def test_household_consent_flags_when_household_yes_without_consent():
    _bulk(Household, [_household("h1", consent="no")])
    run_dq_for_model(DQ_SPECS[Household], write_reports=False)

    assert _issues_for(Household, DataQualityIssue.CONSENT).exists()


# --------------------------------------------------------------------------- #
# Pregnancy / Pregnancy Outcome checks
# --------------------------------------------------------------------------- #


def test_pregnancy_validity_lmp_after_edd():
    _bulk(
        Pregnancy,
        [
            Pregnancy(
                key="p1",
                ea="EA-1",
                enumerator="Enum A",
                PE_03="yes",
                PE_04="HH-1",
                PE_06="Jane Doe",
                PE_09A="2026-06-01",
                PE_10A="2026-01-01",
            )
        ],
    )
    run_dq_for_model(DQ_SPECS[Pregnancy], write_reports=False)

    issue = _issues_for(Pregnancy, DataQualityIssue.VALIDITY).get()
    assert "lmp_after_expected_delivery_date" in issue.details["violations"]


def test_pregnancy_completeness_missing_woman_name():
    _bulk(
        Pregnancy,
        [
            Pregnancy(
                key="p1", ea="EA-1", enumerator="Enum A", PE_03="yes", PE_04="HH-1"
            )
        ],
    )
    run_dq_for_model(DQ_SPECS[Pregnancy], write_reports=False)

    issue = _issues_for(Pregnancy, DataQualityIssue.INCOMPLETE).get()
    assert "PE_06" in issue.details["missing_fields"]


def test_pregnancy_outcome_multiple_birth_consistency():
    _bulk(
        PregnancyOutcome,
        [
            PregnancyOutcome(
                key="po1",
                ea="EA-1",
                enumerator="Enum A",
                consent="yes",
                PO_02="HH-1",
                PO_04="Jane Doe",
                PO_41="2026-01-01",
                PO_45="no",
                PO_46="born alive",
                PO_47="2",
                PO_47A="0",
            )
        ],
    )
    run_dq_for_model(DQ_SPECS[PregnancyOutcome], write_reports=False)

    issue = _issues_for(PregnancyOutcome, DataQualityIssue.VALIDITY).get()
    assert "multiple_birth_count_without_multiple_flag" in issue.details["violations"]


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #


def test_run_dq_for_form_runs_registered_form():
    _bulk(Death, [_death("d1"), _death("d2")])
    summary = run_dq_for_form("death", write_reports=False)

    assert summary is not None
    assert summary["duplicate"] == 1


def test_run_dq_for_form_returns_none_for_unknown_form():
    assert run_dq_for_form("csa_daily_tracker") is None


def test_dq_death_management_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # keep generated reports/ out of the repo
    _bulk(Death, [_death("d1"), _death("d2")])

    call_command("dq_death")

    assert _issues_for(Death, DataQualityIssue.DUPLICATE).exists()
    assert (tmp_path / "reports" / "dq_death.csv").exists()


def test_member_must_match_issue_target_model():
    _bulk(Death, [_death("d1"), _death("d2")])
    run_dq_for_model(DQ_SPECS[Death], write_reports=False)
    issue = _issues_for(Death, DataQualityIssue.DUPLICATE).get()

    pregnancy_ct = ContentType.objects.get_for_model(Pregnancy)
    with pytest.raises(ValueError, match="must match issue.target_model"):
        DataQualityIssueMember(
            issue=issue, content_type=pregnancy_ct, object_id=1
        ).save()


# --------------------------------------------------------------------------- #
# View: each tab shows its own model's issues
# --------------------------------------------------------------------------- #


@pytest.fixture
def view_datacleanup_group():
    perm = Permission.objects.filter(codename="view_datacleanup").first()
    return GroupFactory.create(permissions=[perm])


def test_death_tab_shows_own_issues(settings, view_datacleanup_group):
    settings.QUESTIONS_TO_AUTODETECT_DUPLICATES = "Id10017, Id10010"
    user = UserFactory.create(groups=[view_datacleanup_group])
    _bulk(
        Death,
        [_death("d1", DE_03="Flagged Person"), _death("d2", DE_03="Flagged Person")],
    )
    run_dq_for_model(DQ_SPECS[Death], write_reports=False)

    client = Client()
    client.force_login(user=user)
    response = client.get("/va_data_cleanup/?tab=death")

    assert response.status_code == 200
    assert b"Flagged Person" in response.content
    assert b"Duplicate" in response.content


def test_cleanup_loads_only_the_selected_data_quality_model(
    settings, view_datacleanup_group, monkeypatch
):
    settings.QUESTIONS_TO_AUTODETECT_DUPLICATES = "Id10017, Id10010"
    user = UserFactory.create(groups=[view_datacleanup_group])
    client = Client()
    client.force_login(user=user)
    loaded_models = []
    original = cleanup_views._dq_rows_for_model

    def track_model(request, model, build_row, only_fields):
        loaded_models.append(model)
        return original(request, model, build_row, only_fields)

    monkeypatch.setattr(cleanup_views, "_dq_rows_for_model", track_model)

    response = client.get("/va_data_cleanup/?tab=death")

    assert response.status_code == 200
    assert loaded_models == [Death]
    assert b"Household Data Quality Issues" not in response.content


def test_data_quality_rows_are_paginated(settings, view_datacleanup_group):
    settings.QUESTIONS_TO_AUTODETECT_DUPLICATES = "Id10017, Id10010"
    user = UserFactory.create(groups=[view_datacleanup_group])
    deaths = [_death(f"d{i}", DE_03=f"Person {i}") for i in range(30)]
    _bulk(Death, deaths)
    content_type = ContentType.objects.get_for_model(Death)
    issue = DataQualityIssue.objects.create(
        issue_type=DataQualityIssue.DUPLICATE,
        target_model=content_type,
        signature="pagination-test",
        details={"subtype": "deceased_household_dod"},
    )
    DataQualityIssueMember.objects.bulk_create(
        [
            DataQualityIssueMember(
                issue=issue,
                content_type=content_type,
                object_id=death.id,
            )
            for death in deaths
        ]
    )
    client = Client()
    client.force_login(user=user)

    first_page = client.get("/va_data_cleanup/?tab=death")
    second_page = client.get("/va_data_cleanup/?tab=death&dq_page=2")

    assert first_page.status_code == 200
    assert len(first_page.context["death_issue_rows"]) == cleanup_views.DQ_PER_PAGE
    assert first_page.context["dq_page"].paginator.count == 30
    assert second_page.status_code == 200
    assert len(second_page.context["death_issue_rows"]) == 5
    assert second_page.context["dq_page"].number == 2


# --------------------------------------------------------------------------- #
# dq_all command
# --------------------------------------------------------------------------- #


def test_dq_all_runs_every_dataset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # keep generated reports/ out of the repo
    _bulk(Household, [_household("h1"), _household("h2")])
    _bulk(Death, [_death("d1"), _death("d2")])

    call_command("dq_all")

    assert _issues_for(Household, DataQualityIssue.DUPLICATE).exists()
    assert _issues_for(Death, DataQualityIssue.DUPLICATE).exists()
    # Reports written for each dataset.
    assert (tmp_path / "reports" / "dq_households.csv").exists()
    assert (tmp_path / "reports" / "dq_death.csv").exists()


# --------------------------------------------------------------------------- #
# Verbal Autopsy sub-tabs
# --------------------------------------------------------------------------- #


def _va_cleanup_client(view_datacleanup_group, settings):
    settings.QUESTIONS_TO_AUTODETECT_DUPLICATES = "Id10017, Id10010"
    user = UserFactory.create(groups=[view_datacleanup_group])
    client = Client()
    client.force_login(user=user)
    return client


def _show_url(va):
    return reverse("va_data_management:show", kwargs={"id": va.id}).encode()


def test_va_subtab_nav_renders(settings, view_datacleanup_group):
    client = _va_cleanup_client(view_datacleanup_group, settings)
    response = client.get("/va_data_cleanup/?tab=verbal-autopsy")

    assert response.status_code == 200
    for pane in (
        b"cleanup-va-duplicates",
        b"cleanup-va-coding-issues",
        b"cleanup-va-indeterminate",
        b"cleanup-va-errors",
        b"cleanup-va-missing-gps",
    ):
        assert pane in response.content


def test_va_errors_subtab_lists_error_vas(settings, view_datacleanup_group):
    client = _va_cleanup_client(view_datacleanup_group, settings)
    va = VerbalAutopsyFactory.create()
    CauseOfDeathFactory.create(verbalautopsy=va, cause="HIV/AIDS related death")
    CauseCodingIssueFactory.create(verbalautopsy=va, severity="error")

    response = client.get("/va_data_cleanup/?tab=verbal-autopsy&va_subtab=errors")

    assert response.status_code == 200
    assert _show_url(va) in response.content
    assert b"No VAs with coding errors were found." not in response.content


def test_va_coding_issues_subtab_lists_uncoded_vas(settings, view_datacleanup_group):
    client = _va_cleanup_client(view_datacleanup_group, settings)
    va = VerbalAutopsyFactory.create()  # no CauseOfDeath -> uncoded

    response = client.get(
        "/va_data_cleanup/?tab=verbal-autopsy&va_subtab=coding-issues"
    )

    assert response.status_code == 200
    assert _show_url(va) in response.content


def test_va_indeterminate_subtab_lists_indeterminate_vas(
    settings, view_datacleanup_group
):
    client = _va_cleanup_client(view_datacleanup_group, settings)
    va = VerbalAutopsyFactory.create()
    CauseOfDeathFactory.create(verbalautopsy=va, cause="Indeterminate")

    response = client.get(
        "/va_data_cleanup/?tab=verbal-autopsy&va_subtab=indeterminate"
    )

    assert response.status_code == 200
    assert _show_url(va) in response.content


def test_invalid_va_subtab_falls_back_to_duplicates(settings, view_datacleanup_group):
    client = _va_cleanup_client(view_datacleanup_group, settings)
    response = client.get("/va_data_cleanup/?tab=verbal-autopsy&va_subtab=bogus")

    assert response.status_code == 200
    assert response.context["active_va_subtab"] == "duplicates"


def test_va_missing_gps_subtab_lists_only_blank_geopoint(
    settings, view_datacleanup_group
):
    client = _va_cleanup_client(view_datacleanup_group, settings)
    blank = VerbalAutopsyFactory.create()  # geopoint defaults to blank
    located = VerbalAutopsyFactory.create(geopoint="-15.0 28.0 1000 5")

    response = client.get("/va_data_cleanup/?tab=verbal-autopsy&va_subtab=missing-gps")

    assert response.status_code == 200
    assert (
        b"Verbal-autopsy issue table shared by the Coding Issues / Indeterminate / "
        b"Errors sub-tabs" not in response.content
    )
    assert (
        b"Reusable paginator for pages that host several independent lists."
        b" Expects: page_obj (a Page) and page_param (the query-string key, e.g. "
        b'"err_page").' not in response.content
    )
    assert response.context["va_missing_gps_count"] == 1
    row_ids = {row["id"] for row in response.context["va_missing_gps_rows"]}
    assert row_ids == {blank.id}
    assert located.id not in row_ids
