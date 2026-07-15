"""Tests that VASA feeds the existing InterVA5 coding pipeline correctly."""

import pytest

from va_explorer.va_data_management.models import (
    VASA,
    CauseOfDeath,
    VASASocialAutopsy,
    VerbalAutopsy,
)
from va_explorer.va_data_management.utils import coding
from va_explorer.va_data_management.vasa.model_fields import SA_FIELD_DEFS

SAMPLE_WHO_VALUES = {
    "Id10019": "female",
    "Id10021": "2000-01-01",
    "Id10022": "yes",
    "Id10077": "yes",
    "Id10147": "no",
}


@pytest.mark.django_db
def test_vasa_and_va_produce_identically_shaped_coding_input():
    va = VerbalAutopsy.objects.create(instanceid="uuid:va", **SAMPLE_WHO_VALUES)
    vasa = VASA.objects.create(
        instanceid="uuid:vasa", instrument="adult", **SAMPLE_WHO_VALUES
    )

    va_input = coding.build_pycrossva_records([va])[0]
    vasa_input = coding.build_pycrossva_records([vasa])[0]

    id_keys = lambda row: {k for k in row if k.startswith("-Id")}  # noqa: E731
    assert id_keys(va_input) == id_keys(vasa_input)
    # The shared WHO answers match ...
    for who, value in SAMPLE_WHO_VALUES.items():
        assert vasa_input[f"-{who}"] == value == va_input[f"-{who}"]
    # ... and a WHO Id absent from the VASA form is present but blank (same as a
    # blank VA answer), so the algorithm treats it as missing.
    assert vasa_input.get("-Id10120", "") in ("", None)


@pytest.mark.django_db
def test_raw_answers_provenance_never_enters_coding_input():
    vasa = VASA.objects.create(
        instanceid="uuid:vasa-raw",
        instrument="adult",
        Id10019="female",
        raw_answers={"values": {"Q1601": "2"}, "meta": {"crosswalk_version": "x"}},
    )
    row = coding.build_pycrossva_records([vasa])[0]
    assert "-raw_answers" not in row
    assert row["-Id10019"] == "female"


@pytest.mark.django_db
def test_coding_parity_between_loader_decoded_vasa_and_canonical_va():
    """The in-place decoded values are exactly what coding receives: a VASA
    ingested from coded answers equals a VA created with the decoded values."""
    import pandas as pd

    from va_explorer.va_data_management.utils.vasa_loading import (
        load_vasa_from_dataframes,
    )

    load_vasa_from_dataframes(
        pd.DataFrame(
            [
                {
                    "instanceID": "uuid:parity",
                    "KEY": "uuid:parity",
                    "Q1601": "2",  # -> Id10019 female
                    "A4006": "1",  # -> Id10064 yes
                    "N2001": "",
                    "A4001": "x",  # adult instrument trigger
                }
            ]
        )
    )
    vasa = VASA.objects.get(instanceid="uuid:parity")
    va = VerbalAutopsy.objects.create(
        instanceid="uuid:parity-va", Id10019="female", Id10064="yes", Id10017="x"
    )

    vasa_input = coding.build_pycrossva_records([vasa])[0]
    va_input = coding.build_pycrossva_records([va])[0]
    shared = {k for k in vasa_input if k.startswith("-Id")} & set(va_input)
    diffs = {
        k: (vasa_input[k], va_input[k])
        for k in shared
        if str(vasa_input[k] or "") != str(va_input[k] or "")
    }
    assert diffs == {}


@pytest.mark.django_db
def test_social_autopsy_never_enters_coding_input():
    vasa = VASA.objects.create(instanceid="uuid:vasa-sa", instrument="neonate")
    sa_field = SA_FIELD_DEFS[0][1]
    VASASocialAutopsy.objects.create(vasa=vasa, **{sa_field: "SECRET_SA_VALUE"})

    row = coding.build_pycrossva_records([vasa])[0]
    assert not any(str(v) == "SECRET_SA_VALUE" for v in row.values())
    assert f"-{sa_field}" not in row


@pytest.mark.django_db
def test_run_interva5_stores_results_against_the_vasa_fk(monkeypatch):
    vasa = VASA.objects.create(instanceid="uuid:vasa-code", instrument="adult")

    fake_response = {
        "results": {
            "VA5": [{"ID": ["1"], "CAUSE1": ["Malaria"], "INDET": [0], "LIK1": ["50"]}]
        },
        "errors": [],
        "warnings": [],
    }
    monkeypatch.setattr(
        coding, "_run_pycross_and_interva5", lambda records: fake_response
    )

    causes, _issues = coding.run_interva5([vasa], fk_field="vasa")

    assert len(causes) == 1
    cod = CauseOfDeath.objects.get(pk=causes[0].pk)
    assert cod.vasa_id == vasa.id
    assert cod.verbalautopsy_id is None
    assert cod.cause == "Malaria"
    assert list(vasa.causes.all()) == [cod]
