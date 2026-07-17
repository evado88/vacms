"""Tests for the VA -> Death matching engine (utils/matching) and command."""

import pytest
from django.core.management import call_command

from va_explorer.tests.factories import VASAFactory, VerbalAutopsyFactory
from va_explorer.va_data_management.models import (
    Death,
    MatchMethod,
    MatchStatus,
    VADeathMatch,
    VASASocialAutopsy,
)
from va_explorer.va_data_management.utils.matching import (
    auto_link_verbal_autopsy_to_death,
    evaluate_va,
)
from va_explorer.vacms.cmsmodels.events import Event

pytestmark = pytest.mark.django_db


def _make_death(**kwargs):
    """Create a Death with sensible matching fields (bulk_create avoids the
    post_save signal that hangs tests)."""
    defaults = {
        "DE_03": "John Banda",
        "DE_04": "1980-05-01",
        "DE_05": "male",
        "DE_06": "2024-01-10",
        "DE_07": "Home",
        "province": "Lusaka",
        "district": "Lusaka",
        "ward": "Chaisa",
    }
    defaults.update(kwargs)
    death = Death(**defaults)
    # bulk_create avoids Death's post_save signal (queues a Celery task to an
    # unreachable broker in tests); Postgres populates pk on the instance.
    Death.objects.bulk_create([death])
    return death


@pytest.fixture
def valid_death_id():
    """A Death ID guaranteed to validate against death_ids.is_valid_death_id."""
    from va_explorer.va_data_management.utils.death_ids import (
        build_prefix,
        format_death_id,
    )

    cluster_code = build_prefix("2026")  # 4-digit base (1-9 lead) + 2 checksum
    return format_death_id(cluster_code, 1)


def _set_death_id(death, death_id):
    death.death_id = death_id
    death.save()


def _make_va(**kwargs):
    defaults = {
        "Id10017": "John",
        "Id10018": "Banda",
        "Id10019": "male",
        "Id10021": "1980-05-01",
        "Id10023": "2024-01-10",
        "ward": "Chaisa",
        "district": "Lusaka",
        "province": "Lusaka",
    }
    defaults.update(kwargs)
    return VerbalAutopsyFactory.create(**defaults)


def _make_vasa(death_id=None, **kwargs):
    defaults = {
        "Id10017": "John",
        "Id10018": "Banda",
        "Id10019": "male",
        "Id10021": "1980-05-01",
        "Id10023": "2024-01-10",
        "ward": "Chaisa",
        "district": "Lusaka",
        "province": "Lusaka",
    }
    defaults.update(kwargs)
    vasa = VASAFactory.create(**defaults)
    VASASocialAutopsy.objects.create(vasa=vasa, DeathID=death_id or "")
    return vasa


# --------------------------------------------------------------------------
# VASA exact-key (DeathID) matching
# --------------------------------------------------------------------------
def test_vasa_exact_death_id_matches(valid_death_id):
    death = _make_death()
    _set_death_id(death, valid_death_id)
    vasa = _make_vasa(death_id=valid_death_id)

    result = evaluate_va(vasa)
    assert result.status == MatchStatus.MATCHED_AUTO
    assert result.method == MatchMethod.EXACT_DEATH_ID
    assert result.death_pk == death.pk
    assert result.score == 100.0


def test_vasa_exact_death_id_but_conflicting_sex_goes_to_review(valid_death_id):
    death = _make_death(DE_05="female")
    _set_death_id(death, valid_death_id)
    vasa = _make_vasa(death_id=valid_death_id, Id10019="male")

    result = evaluate_va(vasa)
    # Key matched but demographics materially conflict -> human review.
    assert result.status == MatchStatus.PENDING_REVIEW
    assert result.method == MatchMethod.EXACT_DEATH_ID
    assert "sex" in result.contradictory_fields


def test_vasa_unmatched_when_death_id_blank_and_no_fuzzy_candidate():
    _make_death(ward="Faraway", district="Elsewhere", province="Other")
    vasa = _make_vasa(
        death_id="",
        ward="Nowhere",
        district="Nowhere",
        province="Nowhere",
        Id10017="Zzz",
        Id10018="Qqq",
    )
    result = evaluate_va(vasa)
    assert result.status == MatchStatus.UNMATCHED
    assert result.death_pk is None


# --------------------------------------------------------------------------
# VerbalAutopsy Event-bridge matching
# --------------------------------------------------------------------------
def test_va_event_bridge_matches():
    death = _make_death()
    va = _make_va()
    Event.objects.create(va=va, death=death)

    result = evaluate_va(va)
    assert result.status == MatchStatus.MATCHED_AUTO
    assert result.method == MatchMethod.EVENT_BRIDGE
    assert result.death_pk == death.pk


def test_va_event_bridge_conflicting_death_goes_to_review():
    # Event links a Death for a clearly different person.
    death = _make_death(DE_03="Mary Phiri", DE_05="female", DE_06="2019-06-06")
    va = _make_va(Id10017="John", Id10018="Banda", Id10019="male", Id10023="2024-01-10")
    Event.objects.create(va=va, death=death)

    result = evaluate_va(va)
    assert result.status == MatchStatus.PENDING_REVIEW
    assert result.method == MatchMethod.EVENT_BRIDGE


# --------------------------------------------------------------------------
# Fuzzy demographic matching
# --------------------------------------------------------------------------
def test_fuzzy_name_match_with_consistent_demographics():
    death = _make_death(DE_03="Jon Banda")  # slight spelling variation
    va = _make_va(Id10017="John", Id10018="Banda")
    result = evaluate_va(va)
    # Fixed-scale heuristic scores remain review-only until calibrated.
    assert result.status == MatchStatus.PENDING_REVIEW
    assert result.method == MatchMethod.PROBABILISTIC
    assert result.death_pk == death.pk


def test_fuzzy_conflicting_sex_not_auto_linked():
    _make_death(DE_05="female")
    va = _make_va(Id10019="male")
    result = evaluate_va(va)
    assert result.status != MatchStatus.MATCHED_AUTO


def test_fuzzy_conflicting_dod_not_auto_linked():
    _make_death(DE_06="2020-01-01")
    va = _make_va(Id10023="2024-01-10")
    result = evaluate_va(va)
    assert result.status != MatchStatus.MATCHED_AUTO


def test_facility_va_with_no_death_is_unmatched():
    # No Death rows at all.
    va = _make_va()
    result = evaluate_va(va)
    assert result.status == MatchStatus.UNMATCHED
    assert result.death_pk is None


def test_two_near_tied_candidates_are_ambiguous():
    # Two Deaths that both match the VA strongly and near-equally.
    _make_death(DE_03="John Banda", key="d1")
    _make_death(DE_03="John Banda", key="d2")
    va = _make_va()
    result = evaluate_va(va)
    assert result.status == MatchStatus.AMBIGUOUS
    assert len(result.alternatives) >= 1


def test_missing_values_give_no_positive_evidence():
    # Death with only a name; VA with only a (different) name -> no strong match.
    _make_death(
        DE_04="", DE_05="", DE_06="", ward="", district="", DE_03="Totally Different"
    )
    va = _make_va(
        Id10021="",
        Id10023="",
        Id10019="",
        ward="",
        district="",
        Id10017="Someone",
        Id10018="Else",
    )
    result = evaluate_va(va)
    assert result.status in {MatchStatus.UNMATCHED, MatchStatus.PENDING_REVIEW}
    assert result.status != MatchStatus.MATCHED_AUTO


# --------------------------------------------------------------------------
# Persistence, idempotency, manual protection
# --------------------------------------------------------------------------
def test_apply_persists_match_and_sets_linked_death(valid_death_id):
    death = _make_death()
    _set_death_id(death, valid_death_id)
    vasa = _make_vasa(death_id=valid_death_id)

    result = auto_link_verbal_autopsy_to_death(vasa, save=True, source="test")
    assert result.changed is True
    match = VADeathMatch.objects.get(vasa=vasa)
    assert match.status == MatchStatus.MATCHED_AUTO
    assert match.death_id == death.pk
    assert match.config_snapshot
    assert match.crosswalk_version
    vasa.refresh_from_db()
    assert vasa.linked_death == death


def test_review_status_does_not_set_linked_death():
    _make_death(DE_05="female")
    va = _make_va(Id10019="male", Id10023="2024-01-10")
    # Force a review-band outcome via an Event with a conflicting Death.
    death2 = _make_death(DE_03="Mary Phiri", DE_05="female")
    Event.objects.create(va=va, death=death2)
    result = auto_link_verbal_autopsy_to_death(va, save=True, source="test")
    assert result.status == MatchStatus.PENDING_REVIEW
    va.refresh_from_db()
    assert va.linked_death is None  # review is not a confirmed link


def test_rerun_is_idempotent(valid_death_id):
    death = _make_death()
    _set_death_id(death, valid_death_id)
    vasa = _make_vasa(death_id=valid_death_id)

    first = auto_link_verbal_autopsy_to_death(vasa, save=True, source="test")
    assert first.changed is True
    history_after_first = vasa.va_death_match.history.count()

    second = auto_link_verbal_autopsy_to_death(vasa, save=True, source="test")
    assert second.changed is False
    # No new history row on an unchanged re-evaluation.
    assert vasa.va_death_match.history.count() == history_after_first
    assert VADeathMatch.objects.filter(vasa=vasa).count() == 1


def test_manual_link_not_overwritten(valid_death_id):
    death = _make_death()
    _set_death_id(death, valid_death_id)
    other = _make_death(DE_03="Someone Else", key="other")
    vasa = _make_vasa(death_id=valid_death_id)

    # Simulate a manually-confirmed link to a DIFFERENT death.
    VADeathMatch.objects.create(
        vasa=vasa,
        death=other,
        status=MatchStatus.MATCHED_MANUAL,
        method=MatchMethod.MANUAL,
        is_manual=True,
    )
    result = auto_link_verbal_autopsy_to_death(vasa, save=True, source="test")
    assert result.status == MatchStatus.SKIPPED
    match = VADeathMatch.objects.get(vasa=vasa)
    assert match.death_id == other.pk  # unchanged


def test_matcher_error_preserves_existing_authoritative_link(monkeypatch):
    death = _make_death()
    va = _make_va()
    auto_link_verbal_autopsy_to_death(va, save=True, source="test")
    match = VADeathMatch.objects.get(verbal_autopsy=va)
    assert match.is_linked

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic matching failure")

    monkeypatch.setattr(
        "va_explorer.va_data_management.utils.matching.service._evaluate", fail
    )
    result = auto_link_verbal_autopsy_to_death(
        va, save=True, force_recheck=True, source="test_error"
    )

    assert result.status == MatchStatus.ERROR
    match.refresh_from_db()
    assert match.status == MatchStatus.MATCHED_AUTO
    assert match.death_id == death.pk
    assert match.last_error["message"] == "synthetic matching failure"


# --------------------------------------------------------------------------
# Command: dry-run vs apply, disabled flag
# --------------------------------------------------------------------------
def test_command_dry_run_makes_no_writes(valid_death_id):
    death = _make_death()
    _set_death_id(death, valid_death_id)
    _make_vasa(death_id=valid_death_id)

    call_command("link_deaths_to_vas", "--model", "vasa")
    assert VADeathMatch.objects.count() == 0  # dry-run default


def test_command_apply_persists_only_qualifying(valid_death_id):
    death = _make_death()
    _set_death_id(death, valid_death_id)
    _make_vasa(death_id=valid_death_id)
    # A second VASA with no candidate -> unmatched (still recorded, not linked).
    _make_vasa(
        death_id="",
        ward="Nowhere",
        district="Nowhere",
        province="Nowhere",
        Id10017="Zzz",
        Id10018="Qqq",
    )

    call_command("link_deaths_to_vas", "--model", "vasa", "--apply")
    linked = VADeathMatch.objects.filter(status=MatchStatus.MATCHED_AUTO)
    assert linked.count() == 1
    assert linked.first().death_id == death.pk


def test_ingestion_seam_noop_when_flag_disabled(settings):
    settings.VA_DEATH_MATCH_ENABLED = False
    from va_explorer.va_data_management.utils.matching import schedule_link_for_va_ids

    # Should simply return without enqueuing anything or raising.
    schedule_link_for_va_ids([1, 2, 3], model_name="vasa", source="test")
    assert VADeathMatch.objects.count() == 0


def test_ea_matches_across_va_and_death_formats():
    """VA's bare EA digits match the Death register's ``EA_0<digits>`` form."""
    _make_death(ea="EA_05021031018", ward="Chaisa")
    va = _make_va(ea="5021031018", ward="Chaisa")
    result = evaluate_va(va)
    assert result.status == MatchStatus.MATCHED_AUTO
    assert "ea" in result.supporting_fields


def test_different_ea_is_contradictory_not_supporting():
    _make_death(ea="EA_05021031018")
    va = _make_va(ea="9999999999")
    result = evaluate_va(va)
    assert "ea" in result.contradictory_fields


def test_coded_va_ward_resolves_to_name_for_matching():
    """A VerbalAutopsy with a coded ward matches a Death recorded by ward name."""
    from va_explorer.va_data_management.models import ClusterLocationCodes
    from va_explorer.va_data_management.utils.matching import normalization

    ClusterLocationCodes.objects.create(
        ward="Chaisa", ward_code="502108", cluster_code="5021SD"
    )
    normalization.reset_ward_code_map()  # pick up the row just created

    _make_death(ward="Chaisa")  # Death stores the ward NAME
    va = _make_va(ward="502108")  # VA stores the ward CODE
    result = evaluate_va(va)
    assert result.status == MatchStatus.MATCHED_AUTO
    assert "ward" in result.supporting_fields
    normalization.reset_ward_code_map()
