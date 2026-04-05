"""
Migration 0016 serialised a machine-specific FileSystemStorage path for
Feedback.attachment.  This migration replaces it with a stable callable
reference so that makemigrations --check stays clean on every machine.
"""

from django.db import migrations, models
import va_explorer.users.models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0016_remove_user_denied_permissions_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='feedback',
            name='attachment',
            field=models.FileField(
                blank=True,
                null=True,
                storage=va_explorer.users.models._get_feedback_attachment_storage,
                upload_to=va_explorer.users.models.feedback_attachment_upload_path,
            ),
        ),
    ]
