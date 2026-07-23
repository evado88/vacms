"""Tests for the v2 engine: deterministic rules, union blocking, scoring bands,
rejected-pair exclusion and previous-link demotion."""

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError

from va_explorer.va_data_management.models import (
    MatchingConfig,
    MatchMethod,
    MatchStatus,
    ReviewMode,
    ReviewReason,
    VADeathMatch,
    VADeathMatchRejection,
)
from va_explorer.va_data_management.utils.matching import (
    DeathIndex,
    auto_link_verbal_autopsy_to_death,
    build_va_profile,
    evaluate_va,
    resolve_config,
    run_matching,
)
from va_explorer.va_data_management.utils.matching import service as matching_service
from va_explorer.va_data_management.utils.matching.candidate_selection import (
    union_block_candidates,
)
from va_explorer.va_data_management.utils.matching.results import CandidateScore
from va_explorer.va_data_management.utils.matching.scoring import score_candidate

from .test_va_death_matching import _make_death, _make_va, _make_vasa

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# Stage 1 deterministic rules
# --------------------------------------------------------------------------
def test_nrc_rule_links_deterministically():
    death = _make_death(DE_08="123456/78/9")
    va = _make_va(Id10073="123456789")
    result = evaluate_va(va)
    assert result.status == MatchStatus.MATCHED_AUTO
    assert result.method == MatchMethod.NAME_NRC_EXACT
    assert result.death_pk == death.pk


def test_nrc_rule_requires_uniqueness():
    _make_death(DE_08="123456/78/9", key="d1")
    _make_death(DE_08="123456/78/9", key="d2")
    va = _make_va(Id10073="123456789")
    result = evaluate_va(va)
    # Two register entries share the NRC: never a deterministic link.
    assert result.status == MatchStatus.PENDING_REVIEW
    assert result.review_reason == ReviewReason.NRC_DUPLICATE
    assert result.review_mode == ReviewMode.IN_PERSON


def test_nrc_exact_name_matches_when_name_order_is_swapped():
    death = _make_death(DE_03="Banda John", DE_08="123456/78/9")
    va = _make_va(Id10073="123456789")
    result = evaluate_va(va)
    assert result.status == MatchStatus.MATCHED_AUTO
    assert result.method == MatchMethod.NAME_NRC_EXACT
    assert result.death_pk == death.pk


def test_nrc_probabilistic_name_auto_link_uses_component_thresholds():
    config_row = MatchingConfig.objects.get(name="default", is_active=True)
    config_row.thresholds = {
        **config_row.thresholds,
        "name_nrc_probabilistic_auto_enabled": True,
        "name_nrc_auto_similarity": 90,
        "name_nrc_surname_min_similarity": 95,
        "name_nrc_given_min_similarity": 90,
    }
    config_row.save()
    death = _make_death(DE_03="Jhon Banda", DE_08="123456/78/9")
    va = _make_va(Id10073="123456789")

    result = evaluate_va(va)

    assert result.status == MatchStatus.MATCHED_AUTO
    assert result.method == MatchMethod.NAME_NRC_PROBABILISTIC
    assert result.death_pk == death.pk
    evidence = next(
        comparison["evidence"]
        for comparison in result.comparisons
        if comparison["field"] == "nrc_name_components"
    )
    assert evidence["given_name_min_similarity"] >= 90


def test_nrc_fuzzy_name_remains_review_only_until_enabled():
    _make_death(DE_03="Jhon Banda", DE_08="123456/78/9")
    va = _make_va(Id10073="123456789")
    result = evaluate_va(va)
    assert result.status == MatchStatus.PENDING_REVIEW
    assert result.method == MatchMethod.NAME_NRC_PROBABILISTIC
    assert result.review_mode == ReviewMode.IN_PERSON


def test_same_nrc_and_surname_but_different_given_name_requires_in_person_review():
    death = _make_death(DE_03="Peter Banda", DE_08="123456/78/9")
    va = _make_va(Id10073="123456789", Id10017="John", Id10018="Banda")

    result = evaluate_va(va)

    assert result.status == MatchStatus.PENDING_REVIEW
    assert result.death_pk == death.pk
    assert result.review_reason == ReviewReason.NRC_GIVEN_NAME_CONFLICT
    assert result.review_mode == ReviewMode.IN_PERSON


def test_nrc_with_dod_year_conflict_routes_to_review():
    _make_death(DE_08="123456/78/9", DE_06="2023-12-31")
    va = _make_va(Id10073="123456789", Id10023="2024-01-10")
    result = evaluate_va(va)
    # Key matched; DOD only 10 days apart but the year differs -> review.
    assert result.status == MatchStatus.PENDING_REVIEW
    assert result.method == MatchMethod.NAME_NRC_EXACT


def test_exact_composite_rule():
    death = _make_death(DE_03="Unique Person", DE_08="")
    va = _make_va(Id10017="Unique", Id10018="Person")
    result = evaluate_va(va)
    assert result.status == MatchStatus.MATCHED_AUTO
    assert result.method == MatchMethod.EXACT_COMPOSITE
    assert result.death_pk == death.pk


def test_exact_demographic_gps_composite_links_without_district_match():
    death = _make_death(
        DE_03="Precise Person",
        DE_04="1980-05-01",
        DE_05="male",
        DE_06="2024-01-10",
        DE_07="02",
        DE_08="",
        DE_34="-15.416 28.283 0 5",
        district="Register District",
        ward="Register Ward",
    )
    va = _make_va(
        Id10017="Precise",
        Id10018="Person",
        Id10019="male",
        Id10021="1980-05-01",
        Id10023="2024-01-10",
        Id10058="home",
        geopoint="-15.416 28.283 0 5",
        district="Interview District",
        ward="Interview Ward",
    )

    result = evaluate_va(va)

    assert result.status == MatchStatus.MATCHED_AUTO
    assert result.method == MatchMethod.EXACT_COMPOSITE
    assert result.death_pk == death.pk
    assert {"name", "dod", "dob_or_age", "sex", "place_of_death", "gps"} <= set(
        result.supporting_fields
    )


def test_exact_demographic_gps_composite_accepts_exact_age_when_dob_missing():
    death = _make_death(
        DE_03="Age Matched",
        DE_04="2024-01-01",
        DE_05="female",
        DE_06="2024-01-11",
        DE_07="02",
        DE_08="",
        DE_34="-15.416 28.283 0 5",
        district="Register District",
        ward="Register Ward",
    )
    va = _make_va(
        Id10017="Age",
        Id10018="Matched",
        Id10019="female",
        Id10021="",
        Id10023="2024-01-11",
        Id10058="home",
        ageInDays="10",
        geopoint="-15.416 28.283 0 5",
        district="Interview District",
        ward="Interview Ward",
    )

    result = evaluate_va(va)

    assert result.status == MatchStatus.MATCHED_AUTO
    assert result.method == MatchMethod.EXACT_COMPOSITE
    assert result.death_pk == death.pk


def test_invalid_vasa_death_id_format_never_uses_death_id_rule():
    death = _make_death()
    death.death_id = "BADID12345"  # fails checksum/format validation
    death.save()
    vasa = _make_vasa(death_id="BADID12345")
    result = evaluate_va(vasa)
    assert result.method != MatchMethod.EXACT_DEATH_ID


# --------------------------------------------------------------------------
# Stage 2 union blocking — recall property
# --------------------------------------------------------------------------
def _blocks_for(va, death):
    index = DeathIndex.build()
    assert death.pk in index.deaths
    profile = build_va_profile(va)
    thresholds = resolve_config()["thresholds"]
    candidates, stats = union_block_candidates(profile, index, thresholds=thresholds)
    hit = next((blocks for d, blocks in candidates if d.pk == death.pk), None)
    return hit, stats


BLANK_GEO = {"ward": "", "district": "", "province": "", "ea": "", "constituency": ""}


@pytest.mark.parametrize(
    ("death_kwargs", "va_kwargs", "expected_block"),
    [
        # (a) shares only district + year of death (name/dates differ).
        (
            {"DE_03": "Peter Zulu", "DE_06": "2024-03-01", "ward": "Elsewhere"},
            {
                "Id10017": "Pita",
                "Id10018": "Zoolu",
                "Id10023": "2024-06-01",
                "ward": "",
            },
            "district_year",
        ),
        # (b) shares only constituency + ward.
        (
            {
                **{"DE_06": "", "district": "", "province": ""},
                "constituency": "KAB",
                "ward": "Chaisa",
            },
            {
                "Id10023": "2024-01-10",
                "district": "",
                "province": "",
                "constituency": "KAB",
                "ward": "Chaisa",
            },
            "constituency_ward",
        ),
        # (c) shares only the DOD window — no geography at all.
        (
            {**BLANK_GEO, "DE_06": "2024-01-20"},
            {**BLANK_GEO, "Id10023": "2024-01-10"},
            "dod_window",
        ),
        # (d) misspelled surname caught by the phonetic block (name differs,
        #     DOD far outside every window).
        (
            {"DE_03": "Jon Bandah", "DE_06": "2024-09-01"},
            {"Id10017": "John", "Id10018": "Banda", "Id10023": "2024-09-05"},
            "phonetic_surname_district",
        ),
        # (e) initials + death year (same initials, different names/geo).
        (
            {**BLANK_GEO, "DE_03": "Jane Bwalya", "DE_06": "2024-05-05"},
            {
                **BLANK_GEO,
                "Id10017": "Josephine",
                "Id10018": "Banda",
                "Id10023": "2024-07-07",
            },
            "initials_year",
        ),
    ],
)
def test_every_true_pair_shares_at_least_one_block(
    death_kwargs, va_kwargs, expected_block
):
    death = _make_death(**death_kwargs)
    va = _make_va(**va_kwargs)
    blocks, stats = _blocks_for(va, death)
    assert blocks, f"true pair missed by all blocks (stats={stats})"
    assert expected_block in blocks
    assert stats["total"] >= 1


def test_facility_va_falls_back_to_province_block():
    death = _make_death(district="", ward="", ea="", province="Lusaka")
    va = _make_va(
        community_va="no",  # facility VA
        district="",
        ward="",
        ea="",
        constituency="",
        province="Lusaka",
        Id10023="",  # no DOD either — province is the only signal
        location=None,
    )
    blocks, stats = _blocks_for(va, death)
    assert blocks == ["province"]


def test_phonetic_block_handles_death_name_order():
    death = _make_death(DE_03="Banda Jon", DE_06="2022-01-01")
    va = _make_va(Id10017="John", Id10018="Banda", Id10023="2024-09-05")
    blocks, _ = _blocks_for(va, death)
    assert "phonetic_surname_district" in blocks


def test_sex_conflict_is_retained_as_candidate_evidence():
    death = _make_death(DE_05="female")
    va = _make_va(Id10019="male")
    blocks, _ = _blocks_for(va, death)
    assert blocks


def test_community_exact_composite_requires_nonempty_districts():
    _make_death(DE_08="", district="", province="", ward="")
    va = _make_va(district="", province="", ward="", constituency="", ea="")
    result = evaluate_va(va)
    assert result.method != MatchMethod.EXACT_COMPOSITE


def test_dod_block_window_uses_configured_days():
    death = _make_death(
        **BLANK_GEO,
        DE_03="Alice Phiri",
        DE_06="2024-01-20",
        key="window",
    )
    va = _make_va(
        **BLANK_GEO,
        Id10017="Xavier",
        Id10018="Zulu",
        Id10023="2024-01-10",
    )
    profile = build_va_profile(va)
    index = DeathIndex.build()
    thresholds = resolve_config()["thresholds"]

    narrow, _ = union_block_candidates(
        profile,
        index,
        thresholds={**thresholds, "dod_block_days": 5},
    )
    wide, _ = union_block_candidates(
        profile,
        index,
        thresholds={**thresholds, "dod_block_days": 10},
    )

    assert death.pk not in {candidate.pk for candidate, _ in narrow}
    assert death.pk in {candidate.pk for candidate, _ in wide}


def test_candidate_caps_use_configured_per_block_and_total_limits():
    for offset, name in enumerate(("Alice Phiri", "Brian Tembo", "Carol Zulu"), 1):
        _make_death(
            **BLANK_GEO,
            DE_03=name,
            DE_06=f"2024-01-{10 + offset:02d}",
            key=f"cap-{offset}",
        )
    va = _make_va(
        **BLANK_GEO,
        Id10017="Xavier",
        Id10018="Mwila",
        Id10023="2024-01-10",
    )
    profile = build_va_profile(va)
    index = DeathIndex.build()
    thresholds = resolve_config()["thresholds"]

    per_block_candidates, per_block_stats = union_block_candidates(
        profile,
        index,
        thresholds={
            **thresholds,
            "max_candidates_per_block": 2,
            "max_total_candidates": 10,
        },
    )
    total_candidates, total_stats = union_block_candidates(
        profile,
        index,
        thresholds={
            **thresholds,
            "max_candidates_per_block": 10,
            "max_total_candidates": 1,
        },
    )

    assert len(per_block_candidates) == 2
    assert per_block_stats["dod_window_dropped"] == 1
    assert len(total_candidates) == 1
    assert total_stats["truncated"] == 2


# --------------------------------------------------------------------------
# Scoring bands + config overrides
# --------------------------------------------------------------------------
def test_config_weight_override_changes_outcome():
    death = _make_death(DE_03="Jon Banda")
    va = _make_va(Id10017="John", Id10018="Banda")
    base = resolve_config()
    score_default = score_candidate(va, death, method=None, config=base).score

    crippled = {
        **base,
        "weights": {**base["weights"], "name": 0, "dod": 0},
    }
    score_crippled = score_candidate(va, death, method=None, config=crippled).score
    assert score_crippled != score_default


def test_scoring_uses_date_age_and_contradiction_configuration():
    death = _make_death(
        DE_04="1980-01-01",
        DE_06="2024-01-15",
        district="Muchinga",
    )
    va = _make_va(
        Id10021="",
        ageInDays=str(30 * 365),
        Id10023="2024-01-10",
        district="Lusaka",
    )
    base = resolve_config()

    strict = score_candidate(
        va,
        death,
        method=MatchMethod.PROBABILISTIC,
        config={
            **base,
            "thresholds": {
                **base["thresholds"],
                "tol_dod": 3,
                "age_tol_years": 3,
                "contradiction_penalty_fraction": 1,
            },
        },
    )
    lenient = score_candidate(
        va,
        death,
        method=MatchMethod.PROBABILISTIC,
        config={
            **base,
            "thresholds": {
                **base["thresholds"],
                "tol_dod": 7,
                "age_tol_years": 15,
                "contradiction_penalty_fraction": 0,
            },
        },
    )

    strict_by_field = {
        comparison.field: comparison for comparison in strict.comparisons
    }
    lenient_by_field = {
        comparison.field: comparison for comparison in lenient.comparisons
    }
    assert strict_by_field["dod"].awarded < lenient_by_field["dod"].awarded
    assert (
        strict_by_field["dob_or_age"].awarded < lenient_by_field["dob_or_age"].awarded
    )
    assert strict_by_field["district"].awarded < lenient_by_field["district"].awarded
    assert strict.score < lenient.score


def test_score_and_separation_thresholds_control_match_status(monkeypatch):
    deaths = [
        _make_death(DE_03="Alice Phiri", key="score-1"),
        _make_death(DE_03="Brian Tembo", key="score-2"),
    ]
    va = _make_va(Id10017="Xavier", Id10018="Zulu")
    index = DeathIndex.build()
    scores = {deaths[0].pk: 95.0, deaths[1].pk: 80.0}

    monkeypatch.setattr(
        matching_service.cs,
        "union_block_candidates",
        lambda *args, **kwargs: ([(death, ["test"]) for death in deaths], {}),
    )

    def configured_score(va, death, *, method, **kwargs):
        return CandidateScore(
            death_pk=death.pk,
            death_id=None,
            method=method,
            score=scores[death.pk],
        )

    monkeypatch.setattr(matching_service, "score_candidate", configured_score)
    base = resolve_config()

    def status_for(**threshold_overrides):
        config = {
            **base,
            "thresholds": {**base["thresholds"], **threshold_overrides},
        }
        return evaluate_va(va, config=config, index=index).status

    assert (
        status_for(auto=90, review=70, min_sep=10, tie_margin=5)
        == MatchStatus.MATCHED_AUTO
    )
    assert (
        status_for(auto=90, review=70, min_sep=20, tie_margin=5)
        == MatchStatus.AMBIGUOUS
    )
    assert (
        status_for(auto=90, review=70, min_sep=10, tie_margin=20)
        == MatchStatus.AMBIGUOUS
    )

    scores[deaths[0].pk] = 85.0
    assert (
        status_for(auto=90, review=80, min_sep=10, tie_margin=5)
        == MatchStatus.PENDING_REVIEW
    )
    assert (
        status_for(auto=90, review=86, min_sep=10, tie_margin=5)
        == MatchStatus.UNMATCHED
    )


def test_pregnancy_flags_only_compared_for_female_adults():
    base = resolve_config()
    death_m = _make_death(DE_05="male", DE_16="01", key="m")
    va_m = _make_va(Id10019="male", Id10305="yes")
    cand = score_candidate(va_m, death_m, method=None, config=base)
    preg = next(c for c in cand.comparisons if c.field == "pregnancy")
    assert preg.classification == "missing"  # males: excluded from the vector

    death_f = _make_death(DE_05="female", DE_16="01", key="f")
    va_f = _make_va(Id10019="female", Id10305="yes")
    cand = score_candidate(va_f, death_f, method=None, config=base)
    preg = next(c for c in cand.comparisons if c.field == "pregnancy")
    assert preg.classification in {"exact", "compatible"}
    assert preg.awarded > 0


def test_near_miss_lands_in_review_band():
    # Same name/sex/geo but DOD ~2 months off: strong but not auto-linkable.
    _make_death(DE_06="2024-03-05")
    va = _make_va(Id10023="2024-01-10")
    result = evaluate_va(va)
    assert result.status == MatchStatus.PENDING_REVIEW


def test_review_only_mode_never_asserts_automatic_link():
    death = _make_death()
    va = _make_va()
    summary = run_matching([va], apply=True, review_only=True, source="test")
    match = VADeathMatch.objects.get(verbal_autopsy=va)
    assert summary[MatchStatus.PENDING_REVIEW] == 1
    assert match.status == MatchStatus.PENDING_REVIEW
    assert match.death_id == death.pk
    assert va.linked_death is None


# --------------------------------------------------------------------------
# Rejected-pair exclusion + previous-link demotion
# --------------------------------------------------------------------------
def test_rejected_pair_is_never_reproposed_and_next_best_surfaces():
    best = _make_death(DE_03="John Banda", key="best")
    other = _make_death(DE_03="Jon Bandah", key="other")
    va = _make_va()

    result = auto_link_verbal_autopsy_to_death(va, save=True)
    assert result.death_pk == best.pk

    VADeathMatchRejection.objects.create(verbal_autopsy=va, death=best)
    result = auto_link_verbal_autopsy_to_death(va, save=True, force_recheck=True)
    assert result.death_pk != best.pk
    # The next-best candidate surfaces instead of silence.
    assert result.death_pk == other.pk or result.status in {
        MatchStatus.PENDING_REVIEW,
        MatchStatus.AMBIGUOUS,
        MatchStatus.MATCHED_AUTO,
    }


def test_previous_auto_link_is_demoted_not_silently_unlinked():
    old = _make_death(DE_03="John Banda", key="old")
    va = _make_va()
    auto_link_verbal_autopsy_to_death(va, save=True)
    match = VADeathMatch.objects.get(verbal_autopsy=va)
    assert match.status == MatchStatus.MATCHED_AUTO
    assert match.death_id == old.pk

    # The register entry is edited into a clear mismatch; re-evaluation must
    # not silently unlink — it demotes to review with the old death preserved.
    old.DE_03 = "Completely Different"
    old.DE_05 = "female"
    old.DE_06 = "2019-06-06"
    old.save()

    result = auto_link_verbal_autopsy_to_death(va, save=True, force_recheck=True)
    assert result.status == MatchStatus.PENDING_REVIEW
    assert result.demoted_previous_link
    match.refresh_from_db()
    assert match.status == MatchStatus.PENDING_REVIEW
    assert match.alternatives
    assert match.alternatives[0].get("previous_link") is True


@pytest.mark.parametrize("force_recheck", [False, True])
def test_manual_link_is_never_touched(force_recheck):
    death = _make_death()
    va = _make_va()
    VADeathMatch.objects.create(
        verbal_autopsy=va,
        death=death,
        status=MatchStatus.MATCHED_MANUAL,
        is_manual=True,
        method=MatchMethod.MANUAL,
        score=100.0,
    )
    result = auto_link_verbal_autopsy_to_death(
        va, save=True, force_recheck=force_recheck
    )
    assert result.status == MatchStatus.SKIPPED
    match = VADeathMatch.objects.get(verbal_autopsy=va)
    assert match.status == MatchStatus.MATCHED_MANUAL
    assert match.death_id == death.pk


def test_active_matching_config_overrides_settings():
    # The seeded row (migration 0052) drives thresholds; widen review band and
    # verify a weak single-field pair now routes to review instead of unmatched.
    config = MatchingConfig.objects.get(name="default", is_active=True)
    assert resolve_config()["thresholds"]["auto"] == 88
    config.thresholds = {"auto": 99, "review": 99}
    config.save()
    assert resolve_config()["thresholds"]["auto"] == 99


def test_config_rejects_unknown_keys():
    config = resolve_config()
    config["thresholds"]["typo_threshold"] = 10
    from va_explorer.va_data_management.utils.matching.config import validate_config

    with pytest.raises(ImproperlyConfigured, match="unknown thresholds"):
        validate_config(config)


def test_referenced_config_revision_is_immutable():
    _make_death()
    va = _make_va()
    auto_link_verbal_autopsy_to_death(va, save=True)
    config = MatchingConfig.objects.get(name="default")
    config.thresholds = {**config.thresholds, "review": 55}
    with pytest.raises(ValidationError, match="immutable"):
        config.save()
