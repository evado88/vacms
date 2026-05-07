from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from va_explorer.va_data_management.utils.location_access import (
    bump_location_tree_version,
)


@receiver(post_save, sender="va_data_management.Location")
def _on_location_save(sender, **kwargs):
    bump_location_tree_version()


@receiver(post_delete, sender="va_data_management.Location")
def _on_location_delete(sender, **kwargs):
    bump_location_tree_version()


@receiver(post_save, sender="va_data_management.Death")
def _on_death_created(sender, instance, created, raw, **kwargs):
    # raw=True during loaddata/fixtures — skip to avoid task queuing during migrations
    if not created or raw:
        return
    from va_explorer.va_data_management.tasks import assign_death_ids_task

    assign_death_ids_task.delay()
