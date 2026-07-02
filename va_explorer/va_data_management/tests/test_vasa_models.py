"""Model-level tests for the VASA record, social-autopsy, and repeat models."""

import pytest

from va_explorer.va_data_management.models import (
    VASA,
    VASACareSeekingAdult,
    VASACareSeekingNeonate,
    VASASocialAutopsy,
    VerbalAutopsy,
)
from va_explorer.va_data_management.models.verbal_autopsy import BaseVerbalAutopsy


def test_vasa_and_va_share_the_same_who_columns():
    """Both concrete models inherit the identical WHO Id column set."""
    who = lambda model: {  # noqa: E731
        f.name for f in model._meta.fields if f.name.startswith("Id")
    }
    assert who(VASA) == who(VerbalAutopsy)
    assert issubclass(VASA, BaseVerbalAutopsy)
    assert issubclass(VerbalAutopsy, BaseVerbalAutopsy)


def test_social_autopsy_fields_are_not_on_the_vasa_record():
    """SA answers live on a separate model, never on VASA itself."""
    from va_explorer.va_data_management.vasa.model_fields import SA_FIELD_DEFS

    vasa_fields = {f.name for f in VASA._meta.get_fields()}
    sa_fields = {f.name for f in VASASocialAutopsy._meta.fields}
    for _column, model_field, _label in SA_FIELD_DEFS:
        assert model_field in sa_fields
        assert model_field not in vasa_fields


@pytest.mark.django_db
def test_create_vasa_with_social_autopsy_and_repeats():
    vasa = VASA.objects.create(
        instanceid="uuid:test-1",
        key="uuid:test-1",
        instrument="neonate",
        Id10019="female",
        constituency="Some Constituency",
    )
    # community/facility tag is resolved on save (inherited helper).
    assert vasa.community_va_normalized == "yes"

    sa = VASASocialAutopsy.objects.create(vasa=vasa)
    assert vasa.social_autopsy == sa

    child = VASACareSeekingNeonate.objects.create(
        vasa=vasa, key="uuid:child-1", parent_key="uuid:test-1"
    )
    assert list(vasa.care_seeking_neonate.all()) == [child]
    assert VASACareSeekingAdult.objects.count() == 0


@pytest.mark.django_db
def test_vasa_is_soft_deletable_like_va():
    vasa = VASA.objects.create(instanceid="uuid:test-2", key="uuid:test-2")
    vasa.delete()
    assert VASA.objects.filter(pk=vasa.pk).count() == 0
    assert VASA.all_objects.filter(pk=vasa.pk).count() == 1
