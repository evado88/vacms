from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse

from va_explorer.tests.factories import GroupFactory, NewUserFactory, UserFactory
from va_explorer.users.management.commands.initialize_groups import GROUPS_PERMISSIONS
from va_explorer.users.models import User
from va_explorer.users.views import (
    UserRedirectView,
    UserUpdateView,
    user_create_view,
    user_detail_view,
)

pytestmark = pytest.mark.django_db


def seeded_group(name):
    group, _ = Group.objects.get_or_create(name=name)
    group.permissions.clear()
    for model_class, codenames in GROUPS_PERMISSIONS[name].items():
        content_type = ContentType.objects.get_for_model(model_class)
        permissions = Permission.objects.filter(
            content_type=content_type,
            codename__in=codenames,
        )
        group.permissions.add(*permissions)
    return group


class TestUserRedirectView:
    """
    TODO:
        extracting view initialization code as class-scoped fixture
        would be great if only pytest-django supported non-function-scoped
        fixture db access -- this is a work-in-progress for now:
        https://github.com/pytest-dev/pytest-django/pull/258
    """

    def test_get_redirect_url(self, user: User, rf: RequestFactory):
        view = UserRedirectView()
        request = rf.get("/fake-url")
        request.user = user

        view.request = request

        assert view.get_redirect_url() == "/"


class TestUserDetailView:
    def test_with_view_permission(self, rf: RequestFactory):
        can_view_user = Permission.objects.filter(codename="view_user").first()
        can_view_user_group = GroupFactory.create(permissions=[can_view_user])
        user = UserFactory.create(groups=[can_view_user_group])

        another_user = UserFactory.create()

        request = rf.get("/users/")
        request.user = user

        response = user_detail_view(request, pk=another_user.id)

        assert response.status_code == 200

    # NOTE: All users can view their own profile (detail page), regardless of permission
    def test_can_view_own_profile_without_view_permission(
        self, user: User, rf: RequestFactory
    ):
        no_permissions_group = GroupFactory.create(permissions=[])
        user = UserFactory.create(groups=[no_permissions_group])

        request = rf.get("/users/")
        request.user = user

        response = user_detail_view(request, pk=user.id)

        assert response.status_code == 200

    def test_cannot_view_other_profile_without_view_permission(
        self, user: User, rf: RequestFactory
    ):
        no_permissions_group = GroupFactory.create(permissions=[])
        user = UserFactory.create(groups=[no_permissions_group])
        other_user = UserFactory.create()

        request = rf.get("/users/")
        request.user = user

        with pytest.raises(PermissionDenied):
            user_detail_view(request, pk=other_user.id)

    """
    TODO: The two tests below are more integration tests because we are testing the
    "real" URL and request/response cycle instead of only the view.
    The issue is that Django's RequestFactory doesn't have access to the Middleware:
    Session and authentication attributes must be supplied by the test itself if
    required for the view to function properly. So, for these tests we could add the
    middleware manually if that seems like a better approach.
    """

    def test_not_authenticated(self, user: User, rf: RequestFactory):
        client = Client()

        client.user = AnonymousUser()  # type: ignore
        url = "/users/" + str(user.id)
        response = client.get(url, follow=True)

        assert response.status_code == 200
        # assert b"You must be signed in to view this page" in response.content

    def test_not_valid_password(self, user: User):
        client = Client()

        user = NewUserFactory.create()
        user.set_password("mygreatpassword")
        user.save()

        client.post(
            reverse("account_login"),
            {"login": user.email, "password": "mygreatpassword"},
        )

        url = "/users/" + str(user.id)
        response = client.get(url, follow=True)

        assert response.status_code == 200
        assert (
            b"You must set a new password before you can view this page"
            in response.content
        )


class TestUserIndexView:
    @staticmethod
    def authorized_client():
        permission = Permission.objects.get(
            content_type__app_label="users",
            codename="view_user",
        )
        group = GroupFactory.create(permissions=[permission])
        acting_user = UserFactory.create(name="Directory Administrator", groups=[group])
        client = Client()
        client.force_login(acting_user)
        return client

    def test_filters_users_by_name_case_insensitively(self):
        matching_user = UserFactory.create(name="Alice Mumba")
        UserFactory.create(name="Bernard Phiri")
        client = self.authorized_client()

        response = client.get(reverse("users:index"), {"name": "alice"})

        assert response.status_code == 200
        assert list(response.context["object_list"]) == [matching_user]
        assert response.context["filter_form"].cleaned_data["name"] == "alice"
        assert b'name="role"' in response.content
        assert b"Reset" not in response.content

    def test_filters_users_by_role(self):
        selected_role = GroupFactory.create(name="Selected Role")
        other_role = GroupFactory.create(name="Other Role")
        matching_user = UserFactory.create(name="Selected User", groups=[selected_role])
        UserFactory.create(name="Other User", groups=[other_role])
        client = self.authorized_client()

        response = client.get(reverse("users:index"), {"role": selected_role.pk})

        assert response.status_code == 200
        assert list(response.context["object_list"]) == [matching_user]
        assert response.context["filter_form"].cleaned_data["role"] == selected_role

    def test_combines_name_and_role_filters(self):
        selected_role = GroupFactory.create(name="Combined Selected Role")
        other_role = GroupFactory.create(name="Combined Other Role")
        matching_user = UserFactory.create(
            name="Alice Selected", groups=[selected_role]
        )
        UserFactory.create(name="Alice Other", groups=[other_role])
        UserFactory.create(name="Bernard Selected", groups=[selected_role])
        client = self.authorized_client()

        response = client.get(
            reverse("users:index"),
            {"name": "Alice", "role": selected_role.pk},
        )

        assert response.status_code == 200
        assert list(response.context["object_list"]) == [matching_user]

    def test_applies_filters_before_pagination_and_preserves_query_parameters(self):
        selected_role = GroupFactory.create(name="Paginated Role")
        for index in range(11):
            UserFactory.create(
                name=f"Paged User {index:02d}",
                groups=[selected_role],
            )
        client = self.authorized_client()

        response = client.get(
            reverse("users:index"),
            {"name": "Paged", "role": selected_role.pk, "page": 2},
        )

        assert response.status_code == 200
        assert response.context["page_obj"].paginator.count == 11
        assert len(response.context["object_list"]) == 1
        assert b"name=Paged" in response.content
        assert f"role={selected_role.pk}".encode() in response.content

    def test_full_page_uses_dynamic_filter_markup(self):
        client = self.authorized_client()

        response = client.get(reverse("users:index"))
        content = response.content.decode()

        assert response.status_code == 200
        assert "data-list-filter" in content
        assert "data-list-slot" in content
        old_filter_card_wrapper = (
            '<section class="ou-card ou-mb-3">\n'
            '      <div class="ou-card-body">\n'
            '        <form method="get" class="ou-user-filter-row'
        )
        assert old_filter_card_wrapper not in content
        assert "ou-user-filter-row" in content
        assert "ou-user-filter-controls" in content
        assert "ou-user-filter-actions-panel" in content
        assert "ou-user-filter-field" in content
        assert 'type="search"' in content
        assert "list_table.js" in content
        assert "Reset" not in content
        assert ">Search</button>" not in content

    def test_create_and_import_actions_render_inside_filter_row(self):
        view_permission = Permission.objects.get(
            content_type__app_label="users",
            codename="view_user",
        )
        add_permission = Permission.objects.get(
            content_type__app_label="users",
            codename="add_user",
        )
        group = GroupFactory.create(permissions=[view_permission, add_permission])
        acting_user = UserFactory.create(name="User Manager", groups=[group])
        client = Client()
        client.force_login(acting_user)

        response = client.get(reverse("users:index"))
        content = response.content.decode()

        assert response.status_code == 200
        assert "ou-page-header-actions" not in content
        assert "Create User" in content
        assert "Import Users" in content
        assert content.index("ou-user-filter-controls") < content.index("Name")
        assert content.index("ou-user-filter-actions-panel") < content.index(
            "Create User"
        )
        assert content.index("ou-user-filter-controls") < content.index(
            "ou-user-filter-actions-panel"
        )

    def test_component_request_returns_user_directory_fragment(self):
        matching_user = UserFactory.create(name="Component Alice")
        UserFactory.create(name="Component Bernard")
        client = self.authorized_client()

        response = client.get(
            reverse("users:index"), {"component": "1", "name": "alice"}
        )
        content = response.content.decode()

        assert response.status_code == 200
        assert list(response.context["object_list"]) == [matching_user]
        assert "Component Alice" in content
        assert "Component Bernard" not in content
        assert "data-list-filter" not in content
        assert "list_table.js" not in content


class TestUserCreateView:
    def test_with_valid_permission(self, user: User, rf: RequestFactory):
        can_add_user = Permission.objects.filter(codename="add_user").first()
        can_add_user_group = GroupFactory.create(permissions=[can_add_user])
        user = UserFactory.create(groups=[can_add_user_group])

        request = rf.get("/fake-url/")
        request.user = user

        response = user_create_view(request)

        assert response.status_code == 200

    def test_without_valid_permission(self, user: User, rf: RequestFactory):
        nothing_group = GroupFactory.create(permissions=[])
        user = UserFactory.create(groups=[nothing_group])

        request = rf.get("/users/")
        request.user = user

        with pytest.raises(PermissionDenied):
            user_create_view(request)

    def test_not_authenticated(self, user: User, rf: RequestFactory):
        client = Client()

        client.user = AnonymousUser()  # type: ignore
        url = "/users/"
        response = client.get(url, follow=True)

        assert response.status_code == 200
        # assert b"You must be signed in to view this page" in response.content

    def test_not_valid_password(self, user: User):
        client = Client()

        user = NewUserFactory.create()
        user.set_password("mygreatpassword")
        user.save()

        client.post(
            reverse("account_login"),
            {"login": user.email, "password": "mygreatpassword"},
        )

        response = client.get("/users/", follow=True)

        assert response.status_code == 200
        assert (
            b"You must set a new password before you can view this page"
            in response.content
        )

    def test_data_manager_cannot_access_user_index(self):
        user = UserFactory.create(groups=[seeded_group("Data Managers")])
        client = Client()
        client.force_login(user)

        response = client.get(reverse("users:index"))

        assert response.status_code == 403


def test_data_viewers_group_includes_default_data_view_permissions():
    group = seeded_group("Data Viewers")

    actual_permissions = set(
        group.permissions.filter(
            content_type__app_label="va_data_management"
        ).values_list("codename", flat=True)
    )

    assert {
        "view_verbalautopsy",
        "view_household",
        "view_pregnancy",
        "view_pregnancyoutcome",
        "view_death",
    }.issubset(actual_permissions)


@pytest.mark.parametrize(
    "group_name",
    [("Mortality Surveillance Officer",), ("Community Surveillance Officer",)],
)
def test_mso_and_csa_groups_include_schedule_permissions(group_name):
    group = seeded_group(group_name)

    actual_permissions = set(
        group.permissions.filter(content_type__app_label="vacms").values_list(
            "codename", flat=True
        )
    )

    assert {"view_event", "change_event"}.issubset(actual_permissions)


class TestUserUpdateView:
    def test_get_success_url(self, user: User, rf: RequestFactory):
        view = UserUpdateView()
        request = rf.get("/fake-url/")
        request.user = user

        view.setup(request, pk=user.id)

        assert view.get_success_url() == f"/users/{user.id}/"

    def test_get_object(self, user: User, rf: RequestFactory):
        view = UserUpdateView()
        request = rf.get("/fake-url/")
        request.user = user

        view.setup(request, pk=user.id)

        assert view.get_object() == user

    def test_get_context_data_includes_group_permissions_json(
        self, user: User, rf: RequestFactory
    ):
        admin_group = seeded_group("Admins")
        user.groups.set([admin_group])

        target_user = UserFactory.create(groups=[admin_group])

        view = UserUpdateView()
        request = rf.get(f"/users/update/{target_user.id}/")
        request.user = user

        view.setup(request, pk=target_user.id)
        view.object = target_user

        context = view.get_context_data()

        assert "group_permissions_json" in context
        assert '"Admins"' in context["group_permissions_json"]

    def test_form_valid_does_not_refresh_session_auth_hash(
        self, user: User, rf: RequestFactory
    ):
        admin_group = seeded_group("Admins")
        acting_user = UserFactory.create(groups=[admin_group])
        target_user = UserFactory.create(groups=[admin_group])

        view = UserUpdateView()
        request = rf.post(f"/users/update/{target_user.id}/")
        request.user = acting_user

        view.setup(request, pk=target_user.id)
        view.object = target_user

        with (
            patch(
                "django.contrib.messages.views.SuccessMessageMixin.form_valid",
                return_value=HttpResponse(status=302),
            ) as mock_super,
            patch("va_explorer.users.views.update_session_auth_hash") as mock_hash,
        ):
            response = view.form_valid(Mock())

        assert response.status_code == 302
        mock_super.assert_called_once()
        mock_hash.assert_not_called()


class TestUserSetPasswordView:
    """
    TODO: The two tests below are more integration tests because we are testing the
    "real" URL and request/response cycle instead of only the view.
    The issue is that Django's RequestFactory doesn't have access to the Middleware:
    Session and authentication attributes must be supplied by the test itself if
    required for the view to function properly. So, for these tests we could add the
    middleware manually if that seems like a better approach.
    """

    def test_set_password_without_valid_pw(self, user: User):
        client = Client()

        user = NewUserFactory.create()
        user.set_password("mygreatpassword")
        user.save()

        client.post(
            reverse("account_login"),
            {"login": user.email, "password": "mygreatpassword"},
        )

        url = "/users/set_password"
        response = client.get(url, follow=True)

        assert response.status_code == 200
        assert {
            b"Please type in a password of your choosing to replace your temporary password"
            in response.content
        }

    def test_set_password_with_valid_pw(self, user: User):
        client = Client()

        user = UserFactory.create()
        user.set_password("mygreatpassword")
        user.save()

        client.post(
            reverse("account_login"),
            {"login": user.email, "password": "mygreatpassword"},
        )

        url = "/users/set_password"
        response = client.get(url, follow=True)

        assert response.status_code == 200
        assert b"User has already set password" in response.content
