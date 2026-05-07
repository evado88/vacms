from django.db import migrations


def _has_value(value):
    normalized = ("" if value is None else str(value)).strip().lower()
    return normalized not in {"", "nan", "none", "null"}


def _normalized(record):
    hospital = getattr(record, "hospital", None)
    ward = getattr(record, "ward", None)
    area = getattr(record, "area", None)
    current = getattr(record, "community_va", None)

    if _has_value(hospital):
        return "no"
    if _has_value(ward):
        return "yes"
    if _has_value(area):
        return "yes"

    normalized = ("" if current is None else str(current)).strip().lower()
    if normalized in {"no", "n", "false", "0"}:
        return "no"
    return "yes"


def _recompute(model):
    to_update = []
    for record in model.objects.only(
        "community_va", "hospital", "ward", "area"
    ).iterator(chunk_size=1000):
        value = _normalized(record)
        if (record.community_va or "") != value:
            record.community_va = value
            to_update.append(record)
            if len(to_update) >= 1000:
                model.objects.bulk_update(to_update, ["community_va"], batch_size=1000)
                to_update = []
    if to_update:
        model.objects.bulk_update(to_update, ["community_va"], batch_size=1000)


def forwards(apps, schema_editor):
    _ = schema_editor  # unused
    VerbalAutopsy = apps.get_model("va_data_management", "VerbalAutopsy")
    HistoricalVerbalAutopsy = apps.get_model(
        "va_data_management", "HistoricalVerbalAutopsy"
    )
    _recompute(VerbalAutopsy)
    _recompute(HistoricalVerbalAutopsy)


class Migration(migrations.Migration):
    dependencies = [
        (
            "va_data_management",
            "0023_historicalverbalautopsy_cluster_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
