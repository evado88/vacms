"""
Add database indexes to cms_events fields used heavily by the MSO supervision tab.

  - (event_type, province): composite index for the main death-type province filter
  - (death_id):             index for the death-linked event filter
  - (province):             index for province-scoped access control queries

All indexes created CONCURRENTLY to avoid table locks in production.
atomic=False is required for CONCURRENTLY outside a transaction.
"""
from django.db import migrations


class Migration(migrations.Migration):

    atomic = False  # required for CONCURRENTLY

    dependencies = [
        ("vacms", "0006_admincommandrun"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "cms_events_event_type_province_idx "
                "ON cms_events (event_type, province) "
                "WHERE province IS NOT NULL;"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "cms_events_event_type_province_idx;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "cms_events_death_id_idx "
                "ON cms_events (death_id) "
                "WHERE death_id IS NOT NULL;"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "cms_events_death_id_idx;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "cms_events_province_idx "
                "ON cms_events (province) "
                "WHERE province IS NOT NULL AND province <> '';"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "cms_events_province_idx;"
            ),
        ),
    ]
