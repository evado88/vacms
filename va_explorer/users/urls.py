from django.urls import path

from va_explorer.users.views import (
    UserImportView,
    UserPasswordUpdateView,
    feedback_detail_view,
    feedback_mailbox_view,
    feedback_submit_view,
    user_change_password_view,
    user_create_view,
    user_detail_view,
    user_index_view,
    user_message_detail_view,
    user_message_list_view,
    user_redirect_view,
    user_set_password_view,
    user_update_view,
)

app_name = "users"
urlpatterns = [
    path("~redirect/", view=user_redirect_view, name="redirect"),
    path("update/<int:pk>/", view=user_update_view, name="update"),
    path("create/", view=user_create_view, name="create"),
    path("mailbox/", view=user_message_list_view, name="mailbox"),
    path("mailbox/<int:pk>/", view=user_message_detail_view, name="message_detail"),
    path("feedback/submit/", view=feedback_submit_view, name="feedback_submit"),
    path("feedback/", view=feedback_mailbox_view, name="feedback_mailbox"),
    path(
        "feedback/<int:pk>/",
        view=feedback_detail_view,
        name="feedback_detail",
    ),
    path("", view=user_index_view, name="index"),
    path("<int:pk>/", view=user_detail_view, name="detail"),
    path("set_password/", view=user_set_password_view, name="set_password"),
    path("change_password/", view=user_change_password_view, name="change_password"),
    path("import/", UserImportView, name="import"),
    path("update_password/<int:pk>/", UserPasswordUpdateView, name="update_password"),
]
