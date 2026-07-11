import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command

from va_explorer.va_data_management.models import (
    Household,
    HouseholdDeath,
    HouseholdMember,
    PregnancyOutcome,
    PregnancyOutcomeBaby,
)
from va_explorer.va_data_management.models.data_quality import DataQualityIssue
from va_explorer.va_data_management.utils.repeat_group_dedup import (
    CHILD_DEDUP_SPECS,
    run_dedup_for_model,
)

pytestmark = pytest.mark.django_db


def _household(key="hh-1"):
    return Household.objects.create(key=key, province="Lusaka", district="Chongwe")


def _member(household, key, **overrides):
    data = {
        "household": household,
        "key": key,
        "first_name": "Jane",
        "last_name": "Banda",
        "HH_07": "1990-01-01",
        "HH_05": "Female",
    }
    data.update(overrides)
    return HouseholdMember.objects.create(**data)


def _dup_issue_count(model):
    ct = ContentType.objects.get_for_model(model)
    return DataQualityIssue.objects.filter(
        target_model=ct,
        issue_type=DataQualityIssue.DUPLICATE,
        status=DataQualityIssue.OPEN,
    ).count()


# --------------------------------------------------------------------------- #
# Core member behaviour
# --------------------------------------------------------------------------- #


def test_identical_members_flagged_and_counted_once():
    hh = _household()
    sparse = _member(hh, "k1")
    # Richer row: more non-blank fields -> should be kept canonical.
    rich = _member(hh, "k2", HH_04="Daughter", fullname="Jane Banda", HH_06="Usual")

    summary = run_dedup_for_model(CHILD_DEDUP_SPECS["members"])

    assert summary["groups"] == 1
    assert summary["flagged"] == 1

    rich.refresh_from_db()
    sparse.refresh_from_db()
    # Most-complete row is canonical; the sparse row is flagged.
    assert rich.is_duplicate is False
    assert sparse.is_duplicate is True
    assert sparse.duplicate_of_id == rich.id

    assert HouseholdMember.objects.count() == 2
    assert HouseholdMember.objects.canonical().count() == 1
    assert HouseholdMember.objects.duplicates().count() == 1
    assert _dup_issue_count(HouseholdMember) == 1


def test_ties_break_on_lowest_id():
    hh = _household()
    first = _member(hh, "k1")  # identical completeness
    second = _member(hh, "k2")

    run_dedup_for_model(CHILD_DEDUP_SPECS["members"])

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_duplicate is False  # lowest id wins the tie
    assert second.is_duplicate is True


def test_members_in_different_households_not_grouped():
    hh1, hh2 = _household("hh-1"), _household("hh-2")
    m1 = _member(hh1, "k1")
    m2 = _member(hh2, "k2")

    run_dedup_for_model(CHILD_DEDUP_SPECS["members"])

    m1.refresh_from_db()
    m2.refresh_from_db()
    assert m1.is_duplicate is False
    assert m2.is_duplicate is False
    assert HouseholdMember.objects.canonical().count() == 2


def test_blank_identity_field_is_never_grouped():
    hh = _household()
    # Both rows share name/sex but have no DOB -> identity incomplete.
    _member(hh, "k1", HH_07=None)
    _member(hh, "k2", HH_07=None)

    summary = run_dedup_for_model(CHILD_DEDUP_SPECS["members"])

    assert summary["groups"] == 0
    assert HouseholdMember.objects.canonical().count() == 2


def test_case_and_whitespace_insensitive_match():
    hh = _household()
    a = _member(hh, "k1", first_name="Jane", last_name="Banda")
    b = _member(hh, "k2", first_name="  JANE ", last_name="banda")

    run_dedup_for_model(CHILD_DEDUP_SPECS["members"])

    a.refresh_from_db()
    b.refresh_from_db()
    assert {a.is_duplicate, b.is_duplicate} == {False, True}


# --------------------------------------------------------------------------- #
# Idempotency + self-healing
# --------------------------------------------------------------------------- #


def test_rerun_is_idempotent():
    hh = _household()
    _member(hh, "k1")
    _member(hh, "k2")

    run_dedup_for_model(CHILD_DEDUP_SPECS["members"])
    second = run_dedup_for_model(CHILD_DEDUP_SPECS["members"])

    # Already-flagged rows are not re-flagged, nothing resolved.
    assert second["flagged"] == 0
    assert second["unflagged"] == 0
    assert second["resolved"] == 0
    assert HouseholdMember.objects.canonical().count() == 1
    assert _dup_issue_count(HouseholdMember) == 1


def test_self_heals_when_duplicate_removed():
    hh = _household()
    canonical = _member(hh, "k1", HH_04="Daughter")
    dupe = _member(hh, "k2")

    run_dedup_for_model(CHILD_DEDUP_SPECS["members"])
    dupe.refresh_from_db()
    assert dupe.is_duplicate is True

    # Remove the source of duplication and re-run.
    dupe.delete()
    summary = run_dedup_for_model(CHILD_DEDUP_SPECS["members"])

    canonical.refresh_from_db()
    assert canonical.is_duplicate is False
    assert summary["resolved"] == 1  # stale DQ issue auto-resolved
    assert _dup_issue_count(HouseholdMember) == 0


# --------------------------------------------------------------------------- #
# Deaths and babies
# --------------------------------------------------------------------------- #


def test_household_deaths_dedup():
    hh = _household()
    d1 = HouseholdDeath.objects.create(
        household=hh,
        key="d1",
        deathname="John",
        sirname="Phiri",
        HH_48E="1950-05-05",
        HH_48F="Male",
    )
    d2 = HouseholdDeath.objects.create(
        household=hh,
        key="d2",
        deathname="john",
        sirname=" phiri ",
        HH_48E="1950-05-05",
        HH_48F="Male",
        HH_48G="2026-01-01",
    )

    run_dedup_for_model(CHILD_DEDUP_SPECS["deaths"])

    d1.refresh_from_db()
    d2.refresh_from_db()
    # d2 has the extra HH_48G value -> more complete -> canonical.
    assert d2.is_duplicate is False
    assert d1.is_duplicate is True
    assert HouseholdDeath.objects.canonical().count() == 1


def test_pregnancy_outcome_babies_dedup():
    po = PregnancyOutcome.objects.create(key="po-1")
    b1 = PregnancyOutcomeBaby.objects.create(
        pregnancy_outcome=po, key="b1", PO_49B="Baby Mary", PO_49C="Female"
    )
    b2 = PregnancyOutcomeBaby.objects.create(
        pregnancy_outcome=po,
        key="b2",
        PO_49B="baby mary",
        PO_49C="Female",
        PO_49D="3.2",
    )

    run_dedup_for_model(CHILD_DEDUP_SPECS["babies"])

    b1.refresh_from_db()
    b2.refresh_from_db()
    assert b2.is_duplicate is False  # has birth weight -> more complete
    assert b1.is_duplicate is True
    assert PregnancyOutcomeBaby.objects.canonical().count() == 1


# --------------------------------------------------------------------------- #
# Parent-scoped runs (ingest-time dedup)
# --------------------------------------------------------------------------- #


def test_scoped_run_only_flags_in_scope_parents():
    hh1, hh2 = _household("hh-1"), _household("hh-2")
    _member(hh1, "a1")
    _member(hh1, "a2")
    _member(hh2, "b1")
    _member(hh2, "b2")

    summary = run_dedup_for_model(CHILD_DEDUP_SPECS["members"], parent_ids=["hh-1"])

    assert summary["groups"] == 1
    assert summary["flagged"] == 1
    # hh-2's duplicate pair is untouched by the scoped run.
    assert HouseholdMember.objects.filter(household=hh2, is_duplicate=True).count() == 0


def test_scoped_run_does_not_unflag_or_resolve_out_of_scope():
    hh1, hh2 = _household("hh-1"), _household("hh-2")
    _member(hh1, "a1")
    _member(hh1, "a2")
    _member(hh2, "b1")
    _member(hh2, "b2")

    run_dedup_for_model(CHILD_DEDUP_SPECS["members"])  # full run flags both sets
    assert HouseholdMember.objects.duplicates().count() == 2
    assert _dup_issue_count(HouseholdMember) == 2

    summary = run_dedup_for_model(CHILD_DEDUP_SPECS["members"], parent_ids=["hh-1"])

    # Out-of-scope flags and DQ issues survive; nothing resolved globally.
    assert summary["unflagged"] == 0
    assert summary["resolved"] == 0
    assert HouseholdMember.objects.filter(household=hh2, is_duplicate=True).count() == 1
    assert _dup_issue_count(HouseholdMember) == 2


def test_scoped_run_self_heals_within_scope():
    hh = _household("hh-1")
    _member(hh, "a1", HH_04="Daughter")
    dupe = _member(hh, "a2")

    run_dedup_for_model(CHILD_DEDUP_SPECS["members"], parent_ids=["hh-1"])
    dupe.refresh_from_db()
    assert dupe.is_duplicate is True

    # Row is edited so it no longer matches -> scoped re-run unflags it.
    dupe.first_name = "Mary"
    dupe.save()
    summary = run_dedup_for_model(CHILD_DEDUP_SPECS["members"], parent_ids=["hh-1"])

    dupe.refresh_from_db()
    assert dupe.is_duplicate is False
    assert summary["unflagged"] == 1


def test_scoped_run_with_no_parents_is_a_noop():
    hh = _household()
    _member(hh, "k1")
    _member(hh, "k2")
    run_dedup_for_model(CHILD_DEDUP_SPECS["members"])  # flag + record issue

    summary = run_dedup_for_model(CHILD_DEDUP_SPECS["members"], parent_ids=[None])

    assert summary == {
        "label": "Household Member",
        "total_rows": 0,
        "groups": 0,
        "flagged": 0,
        "unflagged": 0,
        "resolved": 0,
    }
    # Existing flags and issues are untouched.
    assert HouseholdMember.objects.duplicates().count() == 1
    assert _dup_issue_count(HouseholdMember) == 1


# --------------------------------------------------------------------------- #
# Ingest-time dedup via load_household_members
# --------------------------------------------------------------------------- #


def _member_csv(tmp_path, rows, name="members.csv"):
    import pandas as pd

    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def test_load_household_members_flags_duplicates_at_ingest(tmp_path):
    from va_explorer.va_data_management.models import ODKFormChoice

    ODKFormChoice.objects.create(
        form_name="household", field_name="HH_05", value="1", label="Male"
    )
    _household("hh-1")

    row = {
        "PARENT_KEY": "hh-1",
        "first_name": "Jane",
        "last_name": "Banda",
        "HH_07": "1990-01-01",
        "HH_05": "Female",
    }
    rich_row = {**row, "HH_04": "Daughter", "HH_06": "Usual"}
    # Keyless CSV (the common case) containing the same person twice.
    csv_path = _member_csv(tmp_path, [row, rich_row])

    call_command("load_household_members", csv_path, "--log_dir", str(tmp_path))

    assert HouseholdMember.objects.count() == 2
    assert HouseholdMember.objects.canonical().count() == 1
    canonical = HouseholdMember.objects.canonical().get()
    assert canonical.HH_04 == "Daughter"  # most complete row kept unique

    # Re-importing the same keyless CSV flags the new copies too.
    call_command(
        "load_household_members",
        _member_csv(tmp_path, [row, rich_row], name="members2.csv"),
        "--log_dir",
        str(tmp_path),
    )

    assert HouseholdMember.objects.count() == 4
    assert HouseholdMember.objects.canonical().count() == 1


def test_load_household_members_distinct_people_stay_unique(tmp_path):
    from va_explorer.va_data_management.models import ODKFormChoice

    ODKFormChoice.objects.create(
        form_name="household", field_name="HH_05", value="1", label="Male"
    )
    _household("hh-1")

    rows = [
        {
            "PARENT_KEY": "hh-1",
            "first_name": "Jane",
            "last_name": "Banda",
            "HH_07": "1990-01-01",
            "HH_05": "Female",
        },
        {
            "PARENT_KEY": "hh-1",
            "first_name": "John",
            "last_name": "Banda",
            "HH_07": "1988-03-02",
            "HH_05": "Male",
        },
    ]
    call_command(
        "load_household_members",
        _member_csv(tmp_path, rows),
        "--log_dir",
        str(tmp_path),
    )

    assert HouseholdMember.objects.count() == 2
    assert HouseholdMember.objects.canonical().count() == 2
    assert HouseholdMember.objects.duplicates().count() == 0


# --------------------------------------------------------------------------- #
# Command entry point
# --------------------------------------------------------------------------- #


def test_dedupe_child_records_command_all_targets():
    hh = _household()
    _member(hh, "k1")
    _member(hh, "k2")

    call_command("dedupe_child_records", "--target", "all")

    assert HouseholdMember.objects.canonical().count() == 1
