"""Tests for the shared cause-of-death tables that now accept VA *or* VASA."""

import pytest
from django.db import IntegrityError, transaction

from va_explorer.va_data_management.models import (
    VASA,
    CauseCodingIssue,
    CauseOfDeath,
    VerbalAutopsy,
)


def _cod(**kwargs):
    kwargs.setdefault("cause", "Malaria")
    kwargs.setdefault("algorithm", "InterVA5")
    kwargs.setdefault("settings", {})
    return CauseOfDeath.objects.create(**kwargs)


@pytest.mark.django_db
def test_va_parented_cause_is_visible_and_respects_soft_delete():
    va = VerbalAutopsy.objects.create(instanceid="uuid:va-cod")
    cod = _cod(verbalautopsy=va)
    assert CauseOfDeath.objects.filter(pk=cod.pk).exists()
    assert list(va.causes.all()) == [cod]

    va.delete()  # soft delete
    assert not CauseOfDeath.objects.filter(pk=cod.pk).exists()


@pytest.mark.django_db
def test_vasa_parented_cause_is_visible_and_respects_soft_delete():
    va = VerbalAutopsy.objects.create(instanceid="uuid:va-keep")
    va_cod = _cod(verbalautopsy=va)

    vasa = VASA.objects.create(instanceid="uuid:vasa-cod", instrument="adult")
    vasa_cod = _cod(vasa=vasa)
    assert CauseOfDeath.objects.filter(pk=vasa_cod.pk).exists()
    assert list(vasa.causes.all()) == [vasa_cod]

    vasa.delete()  # soft delete VASA
    # The VASA-parented cause is now hidden ...
    assert not CauseOfDeath.objects.filter(pk=vasa_cod.pk).exists()
    # ... but the untouched VA-parented cause is still visible.
    assert CauseOfDeath.objects.filter(pk=va_cod.pk).exists()


@pytest.mark.django_db
def test_coding_issue_supports_vasa_parent():
    vasa = VASA.objects.create(instanceid="uuid:vasa-issue")
    issue = CauseCodingIssue.objects.create(
        vasa=vasa,
        text="missing age",
        severity="warning",
        algorithm="InterVA5",
        settings={},
    )
    assert list(vasa.coding_issues.all()) == [issue]


@pytest.mark.django_db
def test_exactly_one_parent_constraint_rejects_both_and_neither():
    va = VerbalAutopsy.objects.create(instanceid="uuid:va-x")
    vasa = VASA.objects.create(instanceid="uuid:vasa-x")

    with pytest.raises(IntegrityError), transaction.atomic():
        _cod(verbalautopsy=va, vasa=vasa)  # both parents

    with pytest.raises(IntegrityError), transaction.atomic():
        _cod()  # neither parent
