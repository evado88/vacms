from django.contrib import admin

from .models.data_quality import DataQualityIssue, DataQualityIssueMember
from .models.srs_ea_population_target import SRSEAPopulationTarget


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


@admin.register(SRSEAPopulationTarget)
class SRSEAPopulationTargetAdmin(admin.ModelAdmin):
    list_display = (
        "geoid",
        "ward_name",
        "district_name",
        "province_name",
        "rural_urban",
        "population_target",
        "household_target",
        "updated",
    )
    list_filter = ("province_name", "rural_urban")
    search_fields = ("geoid", "ward_name", "district_name", "constituency_name")
    readonly_fields = ("created", "updated")
