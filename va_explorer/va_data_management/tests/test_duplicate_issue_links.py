import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from va_explorer.tests.factories import (
    AdminFactory,
    DeathFactory,
    HouseholdFactory,
    PregnancyFactory,
    PregnancyOutcomeFactory,
    VASAFactory,
    VerbalAutopsyFactory,
)
from va_explorer.va_data_management.models import (
    VASA,
    Death,
    Household,
    Location,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)
from va_explorer.va_data_management.models.data_quality import (
    DataQualityIssue,
    DataQualityIssueMember,
)
from va_explorer.va_data_management.utils.duplicate_issues import (
    get_duplicate_issue_groups,
)

pytestmark = pytest.mark.django_db


DETAIL_CASES = (
    (HouseholdFactory, Household, "household_detail", "Household"),
    (DeathFactory, Death, "death_detail", "Death"),
    (PregnancyFactory, Pregnancy, "pregnancy_detail", "Pregnancy"),
    (
        PregnancyOutcomeFactory,
        PregnancyOutcome,
        "pregnancy_outcome_detail",
        "Pregnancy outcome",
    ),
    (VerbalAutopsyFactory, VerbalAutopsy, "show", "VA"),
    (VASAFactory, VASA, "vasa_show", "VASA"),
)


@pytest.fixture(autouse=True)
def disable_death_id_task(monkeypatch):
    from va_explorer.va_data_management.tasks import assign_death_ids_task

    monkeypatch.setattr(assign_death_ids_task, "delay", lambda *args, **kwargs: None)


def _record_pair(factory_class):
    kwargs = {}
    if factory_class in {VerbalAutopsyFactory, VASAFactory}:
        kwargs["location"] = Location.add_root(
            name="Duplicate test facility",
            key="duplicate-test-facility",
            location_type="facility",
        )
    return factory_class.create(**kwargs), factory_class.create(**kwargs)


def _duplicate_issue(model, *records, status=DataQualityIssue.OPEN):
    content_type = ContentType.objects.get_for_model(model, for_concrete_model=False)
    issue = DataQualityIssue.objects.create(
        issue_type=DataQualityIssue.DUPLICATE,
        target_model=content_type,
        signature=f"detail-duplicate-{model._meta.model_name}-{records[0].pk}",
        title=f"{model._meta.verbose_name.title()} possible duplicate",
        status=status,
    )
    DataQualityIssueMember.objects.bulk_create(
        [
            DataQualityIssueMember(
                issue=issue,
                content_type=content_type,
                object_id=record.pk,
            )
            for record in records
        ]
    )
    return issue


@pytest.mark.parametrize(
    ("factory_class", "model", "route_name", "label"),
    DETAIL_CASES,
    ids=(
        "household",
        "death",
        "pregnancy",
        "pregnancy-outcome",
        "va",
        "vasa",
    ),
)
def test_detail_issues_tab_links_other_duplicate(
    client, factory_class, model, route_name, label
):
    current, duplicate = _record_pair(factory_class)
    issue = _duplicate_issue(model, current, duplicate)
    client.force_login(AdminFactory.create())

    response = client.get(
        reverse(f"va_data_management:{route_name}", kwargs={"id": current.pk})
    )

    assert response.status_code == 200
    assert response.context["duplicate_issue_groups"] == [
        {
            "id": issue.pk,
            "title": f"{model._meta.verbose_name.title()} possible duplicate",
            "records": [
                {
                    "id": duplicate.pk,
                    "label": f"{label} #{duplicate.pk}",
                    "url": reverse(
                        f"va_data_management:{route_name}",
                        kwargs={"id": duplicate.pk},
                    ),
                }
            ],
        }
    ]
    assert b"Possible duplicate records" in response.content


def test_legacy_va_duplicate_flag_links_matching_record(client):
    location = Location.add_root(
        name="Legacy duplicate facility",
        key="legacy-duplicate-facility",
        location_type="facility",
    )
    original = VerbalAutopsyFactory.create(
        location=location,
        unique_va_identifier="shared-legacy-identifier",
    )
    duplicate = VerbalAutopsyFactory.create(
        location=location,
        unique_va_identifier="shared-legacy-identifier",
        duplicate=True,
    )
    client.force_login(AdminFactory.create())

    response = client.get(
        reverse("va_data_management:show", kwargs={"id": duplicate.pk})
    )

    assert response.status_code == 200
    assert response.context["duplicate_issue_groups"][0]["records"] == [
        {
            "id": original.pk,
            "label": f"VA #{original.pk}",
            "url": reverse("va_data_management:show", kwargs={"id": original.pk}),
        }
    ]


def test_duplicate_links_exclude_inaccessible_records():
    current, duplicate = _record_pair(DeathFactory)
    _duplicate_issue(Death, current, duplicate)

    groups = get_duplicate_issue_groups(current, Death.objects.filter(pk=current.pk))

    assert groups == []


def test_duplicate_links_ignore_resolved_issues():
    current, duplicate = _record_pair(PregnancyFactory)
    _duplicate_issue(
        Pregnancy,
        current,
        duplicate,
        status=DataQualityIssue.RESOLVED,
    )

    groups = get_duplicate_issue_groups(current, Pregnancy.objects.all())

    assert groups == []


def test_duplicate_link_resolution_uses_two_queries(django_assert_num_queries):
    current, duplicate = _record_pair(HouseholdFactory)
    _duplicate_issue(Household, current, duplicate)
    ContentType.objects.get_for_model(Household, for_concrete_model=False)

    with django_assert_num_queries(2):
        groups = get_duplicate_issue_groups(current, Household.objects.all())

    assert groups[0]["records"][0]["id"] == duplicate.pk
