from django.contrib import admin

from .models.data_quality import DataQualityIssue, DataQualityIssueMember
from .models.srs_ea_population_target import SRSEAPopulationTarget
from .models.va_death_match import (
    MatchingConfig,
    VADeathMatch,
    VADeathMatchDecision,
    VADeathMatchRejection,
)
from .models.vasa import (
    VASA,
    VASACareSeekingAdult,
    VASACareSeekingChild,
    VASACareSeekingNeonate,
    VASASocialAutopsy,
)


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


@admin.register(VADeathMatch)
class VADeathMatchAdmin(admin.ModelAdmin):
    """Read-only evidence surface; decisions use the locked review workflow."""

    list_display = (
        "id",
        "va",
        "death",
        "status",
        "method",
        "score",
        "score_separation",
        "is_manual",
        "source",
        "last_evaluated_at",
    )
    list_filter = ("status", "method", "is_manual", "source")
    search_fields = ("death_id_snapshot",)
    raw_id_fields = ("verbal_autopsy", "vasa", "death", "confirmed_by", "reviewed_by")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MatchingConfig)
class MatchingConfigAdmin(admin.ModelAdmin):
    """Recalibration surface: edit weights/thresholds here, then run
    ``link_deaths_to_vas --calibrate`` before activating."""

    list_display = ("name", "is_active", "algorithm_version", "updated_by", "updated")
    readonly_fields = ("created", "updated")

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(VADeathMatchRejection)
class VADeathMatchRejectionAdmin(admin.ModelAdmin):
    list_display = ("id", "va", "death", "rejected_by", "created")
    raw_id_fields = ("verbal_autopsy", "vasa", "death", "rejected_by")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VADeathMatchDecision)
class VADeathMatchDecisionAdmin(admin.ModelAdmin):
    list_display = ("id", "action", "va_model", "va_pk", "actor", "created")
    list_filter = ("action", "va_model", "created")
    search_fields = ("va_identifier", "reason", "prior_death_id_snapshot")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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


class VASASocialAutopsyInline(admin.StackedInline):
    model = VASASocialAutopsy
    extra = 0
    can_delete = False


@admin.register(VASA)
class VASAAdmin(admin.ModelAdmin):
    list_display = ("id", "instrument", "source", "instanceid", "location", "created")
    list_filter = ("instrument", "source")
    search_fields = ("instanceid", "key", "Id10017", "Id10018")
    readonly_fields = ("created", "updated")
    inlines = [VASASocialAutopsyInline]


@admin.register(VASACareSeekingNeonate, VASACareSeekingChild, VASACareSeekingAdult)
class VASACareSeekingAdmin(admin.ModelAdmin):
    list_display = ("id", "vasa", "key", "parent_key", "created")
    search_fields = ("key", "parent_key")
    readonly_fields = ("created", "updated")
