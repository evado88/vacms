from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from va_explorer.va_data_management.utils.location_access import bump_location_tree_version


@receiver(post_save, sender="va_data_management.Location")
def _on_location_save(sender, **kwargs):
    bump_location_tree_version()


@receiver(post_delete, sender="va_data_management.Location")
def _on_location_delete(sender, **kwargs):
    bump_location_tree_version()
