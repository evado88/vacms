from django.db import models


class LinkageReview(models.Model):
    """Permission carrier for the VA<->Death linkage review workflow.

    Not backed by a table (same pattern as ``va_data_cleanup.DataCleanup``):
    it exists so group permissions can gate the review queue views.
    """

    class Meta:
        managed = False

        # disable "add", "change", "delete"
        # comma here necessary
        default_permissions = ("view",)

        permissions = (("review_linkage", "Can confirm/reject VA-Death links"),)
