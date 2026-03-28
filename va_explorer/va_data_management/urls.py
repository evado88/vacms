from django.urls import path

from . import views

app_name = "data_management"

urlpatterns = [
    path("", view=views.Index.as_view(), name="index"),
    path("show/<int:id>", view=views.Show.as_view(), name="show"),
    path("edit/<int:id>", view=views.Edit.as_view(), name="edit"),
    path("reset/<int:id>", view=views.Reset.as_view(), name="reset"),
    path(
        "revert_latest/<int:id>",
        view=views.RevertLatest.as_view(),
        name="revert_latest",
    ),
    path(
        "run_coding_algorithms",
        view=views.RunCodingAlgorithm.as_view(),
        name="run_coding_algorithms",
    ),
    path("delete/<int:pk>", view=views.Delete.as_view(), name="delete"),
    path("delete_all", view=views.DeleteAll.as_view(), name="delete_all"),

    path("households", view=views.Households.as_view(), name="households"),
    path("household/<int:id>", view=views.HouseholdDetail.as_view(), name="household_detail",),
    path("household/edit/<int:id>", view=views.HouseholdEdit.as_view(), name="household_edit",),
    path("household/delete/<int:pk>", view=views.HouseholdDelete.as_view(), name="household_delete",),
    path("household/delete_all", view=views.HouseholdDeleteAll.as_view(), name="household_delete_all"),

    path("pregnancies", view=views.Pregnancies.as_view(), name="pregnancies"),
    path("pregnancy/<int:id>", view=views.PregnancyDetail.as_view(), name="pregnancy_detail",),
    path("pregnancy/edit/<int:id>",view=views.PregnancyEdit.as_view(), name="pregnancy_edit",),
    path("pregnancy/delete/<int:pk>", view=views.PregnancyDelete.as_view(), name="pregnancy_delete",),
    path("pregnancy/delete_all", view=views.PregnancyDeleteAll.as_view(), name="pregnancy_delete_all",),

    path("pregnancy-outcomes", view=views.PregnancyOutcomes.as_view(), name="pregnancy_outcomes"),
    path("pregnancy-outcomes/detail/<int:id>", view=views.PregnancyOutcomeDetail.as_view(), name="pregnancy_outcome_detail",),
    path("pregnancy-outcomes/edit/<int:id>",view=views.PregnancyOutcomeEdit.as_view(), name="pregnancy_outcome_edit",),
    path("pregnancy-outcomes/delete/<int:pk>", view=views.PregnancyOutcomeDelete.as_view(), name="pregnancy_outcome_delete",),
    path("pregnancy-outcomes/delete_all", view=views.PregnancyOutcomeDeleteAll.as_view(), name="pregnancy_outcome_delete_all",),

    # Deaths
    path("deaths", view=views.Deaths.as_view(), name="deaths"),
    path("deaths/detail/<int:id>", view=views.DeathDetail.as_view(), name="death_detail"),
    path("deaths/edit/<int:id>", view=views.DeathEdit.as_view(), name="death_edit"),
    path("deaths/delete/<int:pk>", view=views.DeathDelete.as_view(), name="death_delete"),
    path("deaths/delete_all", view=views.DeathDeleteAll.as_view(), name="death_delete_all"),
]
