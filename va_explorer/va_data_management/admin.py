from django.contrib import admin

from .models.data_quality import DataQualityIssue, DataQualityIssueMember


class DataQualityIssueMemberInline(admin.TabularInline):
    model = DataQualityIssueMember
    extra = 0
    readonly_fields = ("content_type", "object_id")
    can_delete = False


@admin.register(DataQualityIssue)
class DataQualityIssueAdmin(admin.ModelAdmin):
    list_display = (
        "issue_type",
        "target_model",
        "status",
        "detected_at",
        "updated",
        "signature",
    )
    list_filter = ("issue_type", "status", "target_model")
    search_fields = ("signature", "title")
    inlines = [DataQualityIssueMemberInline]
    readonly_fields = ("signature", "detected_at", "updated", "resolved_at")
