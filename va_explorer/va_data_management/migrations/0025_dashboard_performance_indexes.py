"""
Add database indexes to fields used heavily by the home dashboard:

- `submissiondate` on Household, Pregnancy, Death, PregnancyOutcome, VerbalAutopsy
  Used for lexicographic range pre-filtering (ISO date text) in the overview KPI
  and chart-series queries.  Without indexes these become full sequential scans.

- `pregnancy_id` on PregnancyOutcome
  Used by the EXISTS subquery in _build_csa_rows that replaces the previous
  full-table load of all outcome IDs into a Python set.

All indexes are created CONCURRENTLY so they do not lock tables in production.
The migration wraps each in its own `RunSQL` because CONCURRENTLY cannot run
inside a transaction, so atomic=False is required.
"""
from django.db import migrations


class Migration(migrations.Migration):

    atomic = False  # required for CONCURRENTLY

    dependencies = [
        ("va_data_management", "0024_recompute_community_va_with_ward"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "va_dm_household_submissiondate_idx "
                "ON va_data_management_household (submissiondate) "
                "WHERE submissiondate IS NOT NULL AND submissiondate <> '';"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "va_dm_household_submissiondate_idx;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "va_dm_pregnancy_submissiondate_idx "
                "ON va_data_management_pregnancy (submissiondate) "
                "WHERE submissiondate IS NOT NULL AND submissiondate <> '';"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "va_dm_pregnancy_submissiondate_idx;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "va_dm_death_submissiondate_idx "
                "ON va_data_management_death (submissiondate) "
                "WHERE submissiondate IS NOT NULL AND submissiondate <> '';"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "va_dm_death_submissiondate_idx;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "va_dm_pregnancyoutcome_submissiondate_idx "
                "ON va_data_management_pregnancyoutcome (submissiondate) "
                "WHERE submissiondate IS NOT NULL AND submissiondate <> '';"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "va_dm_pregnancyoutcome_submissiondate_idx;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "va_dm_pregnancyoutcome_pregnancy_id_idx "
                "ON va_data_management_pregnancyoutcome (pregnancy_id) "
                "WHERE pregnancy_id IS NOT NULL AND pregnancy_id <> '';"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "va_dm_pregnancyoutcome_pregnancy_id_idx;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "va_dm_verbalautopsy_submissiondate_idx "
                "ON va_data_management_verbalautopsy (submissiondate) "
                "WHERE submissiondate IS NOT NULL AND submissiondate <> '';"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "va_dm_verbalautopsy_submissiondate_idx;"
            ),
        ),
    ]
