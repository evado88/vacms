import zipfile
from pathlib import Path

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command

from va_explorer.va_data_management.models import (
    VASA,
    ClusterLocationCodes,
    Death,
    VASASocialAutopsy,
)
from va_explorer.va_data_management.models.data_quality import DataQualityIssue
from va_explorer.va_data_management.utils.death_ids import (
    assign_death_ids,
    build_prefix,
    checksum_letters,
    extract_sequence,
    format_death_id,
    is_valid_cluster_code,
    is_valid_death_id,
    validate_vasa_death_ids,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Pure utility behaviour (checksum, validation, formatting)
# --------------------------------------------------------------------------- #


def test_checksum_worked_example_2026_tl():
    assert checksum_letters("2026") == "TL"
    assert build_prefix("2026") == "2026TL"


def test_checksum_matches_existing_ward_code_convention():
    # 1213SM is the documented example cluster code from the legacy generator.
    assert checksum_letters("1213") == "SM"
    assert is_valid_cluster_code("1213SM")


@pytest.mark.parametrize("bad_base", ["0123", "123", "12345", "12a6", "", None])
def test_checksum_rejects_invalid_bases(bad_base):
    with pytest.raises(ValueError, match="Invalid Death ID base"):
        checksum_letters(bad_base)


def test_valid_death_id_accepted():
    assert is_valid_death_id("2026TL0001")


@pytest.mark.parametrize("suffix", ["0001", "0009", "0010", "0999"])
def test_full_suffix_range_validates(suffix):
    assert is_valid_death_id(f"2026TL{suffix}")


@pytest.mark.parametrize(
    "bad_id",
    [
        "DID-2026-0001",  # legacy/manual format
        "2026TL0000",  # sequence zero
        "2026TL1000",  # suffix must start with 0 (max 0999)
        "2026XX0001",  # wrong checksum letters
        "2026LT0001",  # right letters, wrong order
        "0226TL0001",  # base cannot start with 0
        "2026TL001",  # too short
        "2026TL00011",  # too long
        "",
        None,
    ],
)
def test_malformed_death_ids_rejected(bad_id):
    assert not is_valid_death_id(bad_id)


def test_format_death_id_and_extract_sequence_roundtrip():
    assert format_death_id("2026TL", 10) == "2026TL0010"
    assert extract_sequence("2026TL0010") == 10
    assert extract_sequence("DID-2026-0001") is None


def test_format_death_id_refuses_overflow_and_bad_cluster():
    with pytest.raises(ValueError, match="out of range"):
        format_death_id("2026TL", 1000)
    with pytest.raises(ValueError, match="out of range"):
        format_death_id("2026TL", 0)
    with pytest.raises(ValueError, match="Invalid cluster code"):
        format_death_id("2026XX", 1)


# --------------------------------------------------------------------------- #
# Assignment service
# --------------------------------------------------------------------------- #


def _cluster(ward="CHAMUKA", ward_code="102101", cluster_code="2026TL"):
    return ClusterLocationCodes.objects.create(
        ward=ward, ward_code=ward_code, cluster_code=cluster_code
    )


def _deaths(rows):
    """bulk_create Death rows (never .create(): post_save queues a Celery task)."""
    objs = Death.objects.bulk_create(Death(**row) for row in rows)
    return objs


def test_assigns_ordered_by_de06_then_id():
    _cluster()
    later, earlier, undated = _deaths(
        [
            {"key": "d1", "ward": "CHAMUKA", "DE_03": "Late", "DE_06": "2026-03-02"},
            {"key": "d2", "ward": "CHAMUKA", "DE_03": "Early", "DE_06": "2026-01-05"},
            {"key": "d3", "ward": "CHAMUKA", "DE_03": "Undated", "DE_06": None},
        ]
    )

    summary = assign_death_ids()

    later.refresh_from_db()
    earlier.refresh_from_db()
    undated.refresh_from_db()
    assert summary["assigned"] == 3
    assert earlier.death_id == "2026TL0001"  # earliest DE_06 first
    assert later.death_id == "2026TL0002"
    assert undated.death_id == "2026TL0003"  # unparseable dates sort last
    assert all(is_valid_death_id(d.death_id) for d in (earlier, later, undated))


def test_ward_name_matching_is_case_insensitive_on_name_column():
    # Death.ward holds names; ClusterLocationCodes.ward_code holds numeric
    # codes — the lookup must hit the ward *name* column.
    _cluster(ward="CHAMUKA", ward_code="102101")
    (death,) = _deaths([{"key": "d1", "ward": "chamuka"}])

    assign_death_ids()

    death.refresh_from_db()
    assert death.death_id == "2026TL0001"


def test_mixed_case_ward_variants_share_one_sequence():
    # Regression: the dev DB stores the same ward under two casings. Grouping
    # by raw ward string let both groups resolve to the same cluster prefix
    # and mint the same ID (caught by unique_death_id_when_set in the E2E
    # backfill). Normalised grouping must share a single sequence.
    _cluster()
    a, b = _deaths(
        [
            {"key": "d1", "ward": "CHAMUKA"},
            {"key": "d2", "ward": "Chamuka"},
        ]
    )

    summary = assign_death_ids()

    a.refresh_from_db()
    b.refresh_from_db()
    assert summary["assigned"] == 2
    assert {a.death_id, b.death_id} == {"2026TL0001", "2026TL0002"}


def test_sequence_continues_from_existing_valid_ids():
    _cluster()
    _deaths(
        [
            {"key": "d1", "ward": "CHAMUKA", "death_id": "2026TL0007"},
            {"key": "d2", "ward": "CHAMUKA"},
        ]
    )

    assign_death_ids()

    new = Death.objects.get(key="d2")
    assert new.death_id == "2026TL0008"


def test_malformed_existing_ids_reported_not_poisoning_not_overwritten():
    _cluster()
    _deaths(
        [
            # Malformed but startswith the cluster prefix: huge numeric suffix
            # would poison "max + 1" under the old int() parsing.
            {"key": "d1", "ward": "CHAMUKA", "death_id": "2026TL9999"},
            {"key": "d2", "ward": "CHAMUKA"},
        ]
    )

    summary = assign_death_ids()

    malformed = Death.objects.get(key="d1")
    new = Death.objects.get(key="d2")
    assert malformed.death_id == "2026TL9999"  # never overwritten
    assert "2026TL9999" in summary["malformed_existing"]
    assert new.death_id == "2026TL0001"  # sequence unaffected


def test_missing_cluster_code_skips_ward_with_report():
    (death,) = _deaths([{"key": "d1", "ward": "NOWHERE"}])

    summary = assign_death_ids()

    death.refresh_from_db()
    assert death.death_id in (None, "")
    assert summary["skipped_wards"]["NOWHERE"]["count"] == 1


def test_invalid_cluster_code_in_table_is_refused():
    _cluster(cluster_code="2026XX")  # fails checksum
    (death,) = _deaths([{"key": "d1", "ward": "CHAMUKA"}])

    summary = assign_death_ids()

    death.refresh_from_db()
    assert death.death_id in (None, "")
    assert "checksum" in summary["skipped_wards"]["CHAMUKA"]["reason"]


def test_dry_run_writes_nothing():
    _cluster()
    (death,) = _deaths([{"key": "d1", "ward": "CHAMUKA"}])

    summary = assign_death_ids(dry_run=True)

    death.refresh_from_db()
    assert summary["assigned"] == 1
    assert summary["assignments"][0][1] == "2026TL0001"
    assert death.death_id in (None, "")


def test_rerun_is_idempotent():
    _cluster()
    _deaths([{"key": "d1", "ward": "CHAMUKA"}])

    first = assign_death_ids()
    second = assign_death_ids()

    assert first["assigned"] == 1
    assert second["assigned"] == 0
    assert second["total_missing"] == 0


def test_pk_scoping_only_touches_given_deaths():
    _cluster()
    in_scope, out_of_scope = _deaths(
        [
            {"key": "d1", "ward": "CHAMUKA"},
            {"key": "d2", "ward": "CHAMUKA"},
        ]
    )

    assign_death_ids(death_ids=[in_scope.id])

    in_scope.refresh_from_db()
    out_of_scope.refresh_from_db()
    assert in_scope.death_id == "2026TL0001"
    assert out_of_scope.death_id in (None, "")


def test_sequence_overflow_reported_and_capped():
    _cluster()
    _deaths(
        [
            {"key": "d-full", "ward": "CHAMUKA", "death_id": "2026TL0999"},
            {"key": "d-next", "ward": "CHAMUKA"},
        ]
    )

    summary = assign_death_ids()

    unassigned = Death.objects.get(key="d-next")
    assert unassigned.death_id in (None, "")
    assert summary["overflow_wards"] == [{"ward": "CHAMUKA", "cluster_code": "2026TL"}]


# --------------------------------------------------------------------------- #
# Management command
# --------------------------------------------------------------------------- #


def test_command_dry_run_then_real(capsys):
    _cluster()
    _deaths([{"key": "d1", "ward": "CHAMUKA", "DE_03": "John Doe"}])

    call_command("assign_death_ids", "--dry-run")
    assert Death.objects.get(key="d1").death_id in (None, "")

    call_command("assign_death_ids")
    assert Death.objects.get(key="d1").death_id == "2026TL0001"


# --------------------------------------------------------------------------- #
# generate_ward_codes stays checksum-consistent
# --------------------------------------------------------------------------- #


def test_generated_cluster_codes_validate_as_death_id_prefixes():
    from va_explorer.va_data_management.management.commands.generate_ward_codes import (
        _build_cluster_id_df,
    )

    codes = _build_cluster_id_df(3000)["cluster_id"].tolist()
    assert codes, "generator produced no codes"
    assert all(is_valid_cluster_code(c) for c in codes)
    # Distinct checksum letters (legacy L11 != L12 rule preserved).
    assert all(c[4] != c[5] for c in codes)
    # Legacy readability rules: no zeros, no adjacent repeats.
    assert all("0" not in c[:4] for c in codes)


# --------------------------------------------------------------------------- #
# VASA-side validation (VASA carries a field-entered DeathID)
# --------------------------------------------------------------------------- #


def _vasa_with_death_id(death_id, instanceid):
    vasa = VASA.objects.create(instanceid=instanceid, instrument="facility")
    sa = VASASocialAutopsy.objects.create(vasa=vasa, DeathID=death_id)
    return sa


def _open_validity_issues():
    ct = ContentType.objects.get_for_model(VASASocialAutopsy)
    return DataQualityIssue.objects.filter(
        target_model=ct,
        issue_type=DataQualityIssue.VALIDITY,
        status=DataQualityIssue.OPEN,
    )


def test_vasa_valid_and_registered_death_id_passes():
    _cluster()
    _deaths([{"key": "d1", "ward": "CHAMUKA", "death_id": "2026TL0001"}])
    _vasa_with_death_id("2026TL0001", "uuid:ok")

    summary = validate_vasa_death_ids()

    assert summary == {"checked": 1, "invalid": 0, "unmatched": 0, "resolved": 0}
    assert _open_validity_issues().count() == 0


def test_vasa_invalid_death_id_flagged_but_kept():
    sa = _vasa_with_death_id("DID-2026-0001", "uuid:bad")

    summary = validate_vasa_death_ids()

    sa.refresh_from_db()
    assert sa.DeathID == "DID-2026-0001"  # never mutated
    assert summary["invalid"] == 1
    issue = _open_validity_issues().get()
    assert issue.title == "VASA Death ID invalid"


def test_vasa_unmatched_death_id_flagged():
    _vasa_with_death_id("2026TL0005", "uuid:orphan")  # valid but unregistered

    summary = validate_vasa_death_ids()

    assert summary["unmatched"] == 1
    issue = _open_validity_issues().get()
    assert issue.title == "VASA Death ID not found in death register"


def test_vasa_issue_self_heals_when_death_registered_later():
    _vasa_with_death_id("2026TL0005", "uuid:orphan")
    validate_vasa_death_ids()
    assert _open_validity_issues().count() == 1

    _deaths([{"key": "d1", "ward": "CHAMUKA", "death_id": "2026TL0005"}])
    summary = validate_vasa_death_ids()

    assert summary["unmatched"] == 0
    assert summary["resolved"] == 1
    assert _open_validity_issues().count() == 0


def test_vasa_blank_death_id_not_flagged():
    _vasa_with_death_id("", "uuid:blank")

    summary = validate_vasa_death_ids()

    assert summary == {"checked": 0, "invalid": 0, "unmatched": 0, "resolved": 0}


# --------------------------------------------------------------------------- #
# ODK form constraint stays fixed
# --------------------------------------------------------------------------- #


def test_facility_form_death_id_constraint_accepts_full_suffix_range():
    xlsx = (
        Path(__file__).resolve().parents[2]
        / "static"
        / "data"
        / "znphifacilityvasa_02_final.xlsx"
    )
    with zipfile.ZipFile(xlsx) as zf:
        blob = b"".join(
            zf.read(name) for name in zf.namelist() if name.endswith(".xml")
        ).decode("utf-8", "ignore")
    # The broken alternatives (3-char options under string-length 10) are gone
    # and the checksum alphabet + 4-char suffix alternatives are present.
    assert "(000[1-9]|0[1-9][0-9]|[1-9][0-9][0-9])?" not in blob
    assert "[RGABKYNCLSHZDEJPFTM]" in blob
    assert "(000[1-9]|00[1-9][0-9]|0[1-9][0-9][0-9])" in blob
