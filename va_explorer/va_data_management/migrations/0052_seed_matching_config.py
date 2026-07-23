"""Seed the initial active MatchingConfig row for the v2 matching engine.

Recall-biased starting thresholds per the linkage-hardening spec (≥75% of VAs
are known to have a death record): the review band opens at 60 so borderline
pairs route to human review instead of staying unlinked. Calibration
(``link_deaths_to_vas --calibrate``) refines these values; edits are audited
via simple-history.
"""

from django.db import migrations


def seed_config(apps, schema_editor):
    MatchingConfig = apps.get_model("va_data_management", "MatchingConfig")
    if MatchingConfig.objects.exists():
        return
    MatchingConfig.objects.create(
        name="default",
        is_active=True,
        weights={},  # engine defaults (utils/matching/config.py)
        thresholds={"auto": 88, "review": 60},
        algorithm_version="2.0",
        notes=(
            "Initial v2 recall-biased thresholds (auto 88 / review 60) seeded "
            "by migration 0052; calibrate before loosening further."
        ),
    )


def unseed_config(apps, schema_editor):
    MatchingConfig = apps.get_model("va_data_management", "MatchingConfig")
    MatchingConfig.objects.filter(name="default").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("va_data_management", "0051_historicalvadeathmatch_block_sources_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_config, unseed_config),
    ]
