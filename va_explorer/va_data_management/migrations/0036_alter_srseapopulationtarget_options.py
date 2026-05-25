# Generated manually on 2026-05-17 to rename table to follow Django naming convention

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('va_data_management', '0035_srseapopulationtarget_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE IF EXISTS srs_ea_population_targets RENAME TO va_data_management_srseapopulationtarget;",
            reverse_sql="ALTER TABLE IF EXISTS va_data_management_srseapopulationtarget RENAME TO srs_ea_population_targets;",
        ),
    ]
