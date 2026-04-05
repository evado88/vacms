"""
Add database indexes to fields used heavily by the CSA and MSO supervision tabs.

CSADailyTracker:
  - (province, today): composite index for province-scoped date-range queries
  - (enumerator): index for the grouping key

Pregnancy:
  - (province, "PE_10A"): composite index for overdue-pregnancy province filter

All indexes created CONCURRENTLY to avoid table locks in production.
atomic=False is required for CONCURRENTLY outside a transaction.
"""
from django.db import migrations


class Migration(migrations.Migration):

    atomic = False  # required for CONCURRENTLY

    dependencies = [
        ("va_data_management", "0026_csasupervisortargetmap"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "va_dm_csadailytracker_province_today_idx "
                "ON va_data_management_csadailytracker (province, today) "
                "WHERE province IS NOT NULL AND today IS NOT NULL AND today <> '';"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "va_dm_csadailytracker_province_today_idx;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "va_dm_csadailytracker_enumerator_idx "
                "ON va_data_management_csadailytracker (enumerator) "
                "WHERE enumerator IS NOT NULL AND enumerator <> '';"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "va_dm_csadailytracker_enumerator_idx;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS '
                'va_dm_pregnancy_province_pe10a_idx '
                'ON va_data_management_pregnancy (province, "PE_10A") '
                'WHERE province IS NOT NULL AND "PE_10A" IS NOT NULL AND "PE_10A" <> \'\';'
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "va_dm_pregnancy_province_pe10a_idx;"
            ),
        ),
    ]
