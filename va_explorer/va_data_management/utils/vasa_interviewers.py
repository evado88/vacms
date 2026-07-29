"""Resolve VASA interviewer IDs to Mortality Surveillance Officer names."""

from django.db.models.functions import Upper

from va_explorer.users.models import User
from va_explorer.va_data_management.models import VASASocialAutopsy

MSO_GROUP_NAME = "Mortality Surveillance Officer"


def normalize_mso_id(value):
    return str(value or "").strip().upper()


def mso_names_by_worker_id(worker_ids=None):
    """Return normalized MSO IDs mapped to their best available display name."""
    normalized_ids = None
    if worker_ids is not None:
        normalized_ids = {normalize_mso_id(value) for value in worker_ids}
        normalized_ids.discard("")
        if not normalized_ids:
            return {}

    users = (
        User.objects.annotate(normalized_worker_id=Upper("worker_id"))
        .filter(groups__name=MSO_GROUP_NAME)
        .exclude(worker_id__isnull=True)
        .exclude(worker_id="")
    )
    if normalized_ids is not None:
        users = users.filter(normalized_worker_id__in=normalized_ids)

    return {
        user.normalized_worker_id: user.name or user.get_full_name() or user.email
        for user in users.distinct()
    }


def vasa_interviewer_names(vasa_ids):
    """Return VASA primary keys mapped to MSO display names via Q1302."""
    worker_id_by_vasa = {
        vasa_id: normalize_mso_id(worker_id)
        for vasa_id, worker_id in VASASocialAutopsy.objects.filter(
            vasa_id__in=vasa_ids
        ).values_list("vasa_id", "Q1302")
        if normalize_mso_id(worker_id)
    }
    users_by_worker_id = mso_names_by_worker_id(worker_id_by_vasa.values())
    return {
        vasa_id: users_by_worker_id[worker_id]
        for vasa_id, worker_id in worker_id_by_vasa.items()
        if worker_id in users_by_worker_id
    }
