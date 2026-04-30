from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("va_data_management", "0032_add_submissiondate_indexes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "DROP INDEX IF EXISTS pregnancy_submission_idx; "
                        'CREATE INDEX IF NOT EXISTS pregnancy_lmp_idx '
                        'ON va_data_management_pregnancy ("PE_09A");'
                    ),
                    reverse_sql=(
                        "DROP INDEX IF EXISTS pregnancy_lmp_idx; "
                        "CREATE INDEX IF NOT EXISTS pregnancy_submission_idx "
                        "ON va_data_management_pregnancy (submissiondate);"
                    ),
                ),
            ],
            state_operations=[
                migrations.RemoveIndex(
                    model_name="pregnancy",
                    name="pregnancy_submission_idx",
                ),
                migrations.AddIndex(
                    model_name="pregnancy",
                    index=models.Index(fields=["PE_09A"], name="pregnancy_lmp_idx"),
                ),
            ],
        ),
    ]
