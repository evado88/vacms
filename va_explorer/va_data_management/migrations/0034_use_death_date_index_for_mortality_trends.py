from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("va_data_management", "0033_use_lmp_index_for_pregnancy_trends"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "DROP INDEX IF EXISTS death_submission_idx; "
                        'CREATE INDEX IF NOT EXISTS death_date_idx '
                        'ON va_data_management_death ("DE_06");'
                    ),
                    reverse_sql=(
                        "DROP INDEX IF EXISTS death_date_idx; "
                        "CREATE INDEX IF NOT EXISTS death_submission_idx "
                        "ON va_data_management_death (submissiondate);"
                    ),
                ),
            ],
            state_operations=[
                migrations.RemoveIndex(
                    model_name="death",
                    name="death_submission_idx",
                ),
                migrations.AddIndex(
                    model_name="death",
                    index=models.Index(fields=["DE_06"], name="death_date_idx"),
                ),
            ],
        ),
    ]
