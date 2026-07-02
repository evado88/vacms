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
