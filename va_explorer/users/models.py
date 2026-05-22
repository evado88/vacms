import logging
import os
import platform
import uuid
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser, Permission
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models import ManyToManyField
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

# from allauth.account.models import EmailAddress
# from allauth.account.signals import email_confirmed
# from django.dispatch import receiver
from va_explorer.va_data_management.models import Location, VerbalAutopsy
from va_explorer.va_data_management.utils.location_access import (
    restrict_va_queryset_to_user_locations,
)

from .constants import FEEDBACK_MODULE_FEATURES

logger = logging.getLogger(__name__)


# Return a new FileSystemStorage instance at call time so Django serializes
# only the callable reference (a stable dotted path) rather than the
# constructor arguments, which include a machine-specific absolute path and
# would otherwise cause a new alter_feedback_attachment migration on every
# machine that runs makemigrations.
def _get_feedback_attachment_storage():
    upload_root = (
        Path(
            getattr(
                settings, "MEDIA_ROOT", Path(__file__).resolve().parents[2] / "media"
            )
        )
        / "feedback"
        / "uploads"
    )
    return FileSystemStorage(
        location=str(upload_root),
        base_url=f"{settings.MEDIA_URL}feedback/uploads/",
    )


def feedback_attachment_upload_path(instance, filename):
    """
    Generate a predictable upload path for feedback attachments.
    Organizes files by year/month and bakes in useful context for admins.
    """
    now = timezone.now()
    name, ext = os.path.splitext(filename)
    module = getattr(instance, "module", "feedback") or "feedback"
    severity = getattr(instance, "severity", "unspecified") or "unspecified"
    reporter = (
        getattr(getattr(instance, "submitted_by", None), "username", "") or "anonymous"
    )
    safe_name = "-".join(
        part for part in [module, severity, reporter, slugify(name)] if part
    )
    return "/".join(
        [
            now.strftime("%Y"),
            now.strftime("%m"),
            f"{safe_name}-{uuid.uuid4().hex[:8]}{ext}",
        ]
    )


class CustomUserManager(BaseUserManager):
    """
    Custom user model manager where email is the unique identifiers
    for authentication instead of username.
    """

    def create_user(self, email, password, **extra_fields):
        """
        Create and save a User with the given email and password.
        """
        if not email:
            raise ValueError(_("Email is required"))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password, **extra_fields):
        """
        Create and save a SuperUser with the given email and password.
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Superuser must have is_staff=True."))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Superuser must have is_superuser=True."))
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_password = self.password

    email = models.EmailField(_("email address"), unique=True)
    name = models.CharField(_("Name of User"), blank=True, max_length=255)
    # start new fields for case management
    mobile1 = models.CharField(_("Mobile 1"), blank=True, max_length=255)
    mobile2 = models.CharField(_("Mobile 2"), blank=True, max_length=255)
    address = models.CharField(_("Address"), blank=True, max_length=255)
    worker_id = models.CharField(
        _("Worker ID"), null=True, blank=True, unique=True, max_length=255
    )  # ID to be used in ODK for assignment.
    # end new fields
    has_valid_password = models.BooleanField(
        _("The user has a user-defined password"), default=False
    )
    denied_permissions = models.JSONField(blank=True, default=list)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    location_restrictions = ManyToManyField(
        Location, related_name="users", db_table="users_user_location_restrictions"
    )

    # The query set of verbal autopsies that this user has access to, based on
    # location restrictions
    # Note: locations are organized in a tree structure, and users have access
    # to all children of any parent location nodes they have access to
    def verbal_autopsies(self, date_cutoff=None, end_date=None):
        # only pull in VAs after certain time period. By default, everything after
        # 1901 (should be everything)
        date_cutoff = date_cutoff if date_cutoff else "1901-01-01"
        end_date = end_date if end_date else datetime.today().strftime("%Y-%m-%d")
        va_objects = VerbalAutopsy.objects.filter(
            Id10023__gte=date_cutoff, Id10023__lte=end_date
        )

        return restrict_va_queryset_to_user_locations(va_objects, self)

    def is_fieldworker(self):
        return self.groups.filter(name="Field Workers").exists()

    @property
    def can_view_pii(self):
        # Gates personally identifiable information including names, contact details,
        # and precise GPS coordinates surfaced on dashboard maps.
        return self.has_perm("va_analytics.view_pii")

    @can_view_pii.setter
    def can_view_pii(self, value):
        permission = Permission.objects.get(
            content_type__app_label="va_analytics", codename="view_pii"
        )
        if value:
            self.user_permissions.add(permission)
        else:
            self.user_permissions.remove(permission)

    @property
    def can_download_csv_exports(self):
        # Gates the "Download CSV" buttons/endpoints across the dashboards,
        # event scheduling, and deaths list. Restricted to Data Managers and
        # Admins (and superusers).
        return bool(
            self.is_superuser
            or self.groups.filter(name__in=["Data Managers", "Admins"]).exists()
        )

    @property
    def can_download_data(self):
        return self.has_perm("va_analytics.download_data")

    @can_download_data.setter
    def can_download_data(self, value):
        permission = Permission.objects.get(
            content_type__app_label="va_analytics", codename="download_data"
        )
        if value:
            self.user_permissions.add(permission)
        else:
            self.user_permissions.remove(permission)

    @property
    def can_supervise_users(self):
        return self.has_perm("va_analytics.supervise_users")

    @can_supervise_users.setter
    def can_supervise_users(self, value):
        permission = Permission.objects.get(
            content_type__app_label="va_analytics", codename="supervise_users"
        )
        if value:
            self.user_permissions.add(permission)
        else:
            self.user_permissions.remove(permission)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def __str__(self):
        return f"{self.name} - {self.email}"

    def get_absolute_url(self):
        """Get url for user's detail view.

        Returns:
            str: URL for user detail.

        """
        return reverse("users:detail", kwargs={"pk": self.pk})

    def has_perm(self, perm, obj=None):
        # Per-user explicit denials override role and direct grants.
        if perm and perm in set(self.denied_permissions or []):
            return False
        return super().has_perm(perm, obj=obj)

    # TODO: Remove if we do not require email confirmation; we will no longer
    # need the lines below
    # def add_email_address(self, request, new_email):
    #     return EmailAddress.objects.add_email(
    #         request, self.user, new_email, confirm=True
    #     )
    #
    # @receiver(email_confirmed)
    # def update_user_email(sender, email_address, **kwargs):
    #     email_address.set_as_primary()
    #
    #     EmailAddress.objects.filter(
    #         user=email_address.user).exclude(primary=True).delete()

    def save(self, *args, **kwargs):
        # TODO: May need to be changed depending on how username comes in from ODK?
        if not self.username:
            self.username = self.email
        super().save(*args, **kwargs)
        if self.original_password != self.password:
            UserPasswordHistory.remember_password(self)

    @property
    def unread_messages(self):
        return self.messages.unread()

    @property
    def mailbox_unread_count(self):
        return self.unread_messages.count()


class UserPasswordHistory(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    old_password = models.CharField(max_length=128)
    auto_now_add = True

    @classmethod
    def remember_password(cls, user):
        cls(user=user, old_password=user.password).save()


class UserMessageQuerySet(models.QuerySet):
    def unread(self):
        return self.filter(read_at__isnull=True)


class UserMessageManager(models.Manager.from_queryset(UserMessageQuerySet)):
    pass


class UnreadUserMessageManager(UserMessageManager):
    def get_queryset(self):
        return super().get_queryset().unread()


class UserMessage(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="messages",
        related_query_name="message",
    )
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(blank=True, null=True)

    objects = UserMessageManager()
    unread = UnreadUserMessageManager()

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        subject = self.subject or _("Message")
        return f"{subject} ({self.user})"

    def mark_read(self):
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at"])

    def get_absolute_url(self):
        return reverse("users:message_detail", kwargs={"pk": self.pk})


class Feedback(models.Model):
    class Module(models.TextChoices):
        DATA_MANAGEMENT = "data_management", "Data Management"
        PERSONNEL_MANAGEMENT = "personnel_management", "Personnel Management"
        SCHEDULE_MANAGEMENT = "schedule_management", "Schedule Management"
        ANALYTICS = "analytics", "Dashboards (Analytics)"

    class ReportType(models.TextChoices):
        BUG = "bug", "Bug"
        FEATURE = "feature", "Feature Request"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        NEW = "new", "New"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"

    subject = models.CharField(max_length=255)
    module = models.CharField(max_length=64, choices=Module.choices)
    feature = models.CharField(max_length=64)
    severity = models.CharField(
        max_length=16, choices=Severity.choices, default=Severity.LOW
    )
    description = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    report_type = models.CharField(
        max_length=16, choices=ReportType.choices, default=ReportType.BUG
    )
    attachment = models.FileField(
        upload_to=feedback_attachment_upload_path,
        storage=_get_feedback_attachment_storage,
        null=True,
        blank=True,
    )
    metadata = models.JSONField(blank=True, default=dict)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="feedback_reports",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.subject} ({self.get_module_display()})"

    @staticmethod
    def module_feature_map():
        return FEEDBACK_MODULE_FEATURES

    @classmethod
    def feature_choices_for(cls, module):
        return cls.module_feature_map().get(module, [])

    def get_feature_display(self):
        return dict(self.feature_choices_for(self.module)).get(
            self.feature, self.feature
        )

    @staticmethod
    def collect_system_metadata(request):
        user_agent = request.headers.get("user-agent", "")
        return {
            "operating_system": platform.platform(),
            "python_version": platform.python_version(),
            "hostname": platform.node(),
            "user_agent": user_agent,
            "ip_address": request.META.get("REMOTE_ADDR"),
            "path": (
                request.build_absolute_uri()
                if hasattr(request, "build_absolute_uri")
                else ""
            ),
            "timestamp": timezone.now().isoformat(),
        }


def generate_unique_worker_id():
    """
    Generate a unique worker ID using checksum validation logic.
    Format: CheckChar + 3 digits (e.g., A123)

    Returns:
        str: A unique worker ID not currently in use
    """
    checksum_map = {
        2: "A",
        3: "B",
        7: "C",
        12: "D",
        13: "E",
        16: "F",
        1: "G",
        10: "H",
        14: "J",
        4: "K",
        8: "L",
        18: "M",
        6: "N",
        15: "P",
        0: "R",
        9: "S",
        17: "T",
        5: "Y",
        11: "Z",
    }

    # Get all existing worker IDs
    existing_ids = set(
        User.objects.filter(worker_id__isnull=False).values_list("worker_id", flat=True)
    )

    # Generate IDs from 001 to 999
    for idc in range(1, 1000):
        # Skip if not 3 digits
        if len(str(idc)) != 3:
            continue

        # Get individual digits
        idc_str = str(idc).zfill(3)
        p1, p2, p3 = int(idc_str[0]), int(idc_str[1]), int(idc_str[2])

        # Skip if all digits are the same (e.g., 111, 222)
        if p1 == p2 == p3:
            continue

        # Calculate checksum
        check_num = (p1 * 9 + p2 * 7 + p3 * 5) % 19

        # Get check character
        check_chr = checksum_map.get(check_num)
        if not check_chr:
            continue

        # Build staff ID
        worker_id = f"{check_chr}{idc_str}"

        # Check if this ID is already used
        if worker_id not in existing_ids:
            return worker_id

    # If all IDs are exhausted (unlikely), raise an error
    raise ValueError("All worker IDs have been exhausted")


class UserActivity(models.Model):
    """Append-only audit trail of significant system actions.

    Originally captured only authentication + page-view events; it now backs the
    Admin Panel "System Audit Trail" tab and records user/permission changes,
    data imports, exports/downloads and background-task events. Always write via
    :meth:`log` so request context is captured consistently and failures never
    break the caller's workflow.
    """

    # --- Action categories (used for grouping + readable "Category > Action") ---
    CATEGORY_AUTH = "auth"
    CATEGORY_USER = "user"
    CATEGORY_DATA = "data"
    CATEGORY_FORM = "form"
    CATEGORY_SYSTEM = "system"

    CATEGORY_CHOICES = [
        (CATEGORY_AUTH, "Authentication"),
        (CATEGORY_USER, "User"),
        (CATEGORY_DATA, "Data"),
        (CATEGORY_FORM, "Form"),
        (CATEGORY_SYSTEM, "System"),
    ]

    # --- Actions ---
    # Auth
    LOGIN = "login"
    LOGOUT = "logout"
    PAGE_VIEW = "page_view"
    LOGIN_FAILED = "login_failed"
    # User
    PERMISSION_CHANGED = "permission_changed"
    ROLE_CHANGED = "role_changed"
    PROFILE_UPDATED = "profile_updated"
    # Data
    DATA_CREATE = "data_create"
    DATA_UPDATE = "data_update"
    DATA_DELETE = "data_delete"
    DATA_IMPORT = "data_import"
    DATA_EXPORT = "data_export"
    # Form
    FORM_PUBLISH = "form_publish"
    FORM_DRAFT = "form_draft"
    FORM_DOWNLOAD = "form_download"
    # System
    SETTINGS_CHANGED = "settings_changed"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"

    ACTION_CHOICES = [
        (LOGIN, "Log in"),
        (LOGOUT, "Log out"),
        (PAGE_VIEW, "Page View"),
        (LOGIN_FAILED, "Failed login"),
        (PERMISSION_CHANGED, "Permission changed"),
        (ROLE_CHANGED, "Role changed"),
        (PROFILE_UPDATED, "Profile updated"),
        (DATA_CREATE, "Create record"),
        (DATA_UPDATE, "Update record"),
        (DATA_DELETE, "Delete record"),
        (DATA_IMPORT, "Import data"),
        (DATA_EXPORT, "Export/download data"),
        (FORM_PUBLISH, "Publish draft"),
        (FORM_DRAFT, "Create or update draft"),
        (FORM_DOWNLOAD, "Download submissions"),
        (SETTINGS_CHANGED, "Settings changed"),
        (TASK_STARTED, "Background task started"),
        (TASK_COMPLETED, "Background task completed"),
        (TASK_FAILED, "Background task failed"),
    ]

    # Map each action to its category so callers usually don't pass it explicitly.
    CATEGORY_FOR_ACTION = {
        LOGIN: CATEGORY_AUTH,
        LOGOUT: CATEGORY_AUTH,
        PAGE_VIEW: CATEGORY_AUTH,
        LOGIN_FAILED: CATEGORY_AUTH,
        PERMISSION_CHANGED: CATEGORY_USER,
        ROLE_CHANGED: CATEGORY_USER,
        PROFILE_UPDATED: CATEGORY_USER,
        DATA_CREATE: CATEGORY_DATA,
        DATA_UPDATE: CATEGORY_DATA,
        DATA_DELETE: CATEGORY_DATA,
        DATA_IMPORT: CATEGORY_DATA,
        DATA_EXPORT: CATEGORY_DATA,
        FORM_PUBLISH: CATEGORY_FORM,
        FORM_DRAFT: CATEGORY_FORM,
        FORM_DOWNLOAD: CATEGORY_FORM,
        SETTINGS_CHANGED: CATEGORY_SYSTEM,
        TASK_STARTED: CATEGORY_SYSTEM,
        TASK_COMPLETED: CATEGORY_SYSTEM,
        TASK_FAILED: CATEGORY_SYSTEM,
    }

    # --- Status ---
    STATUS_SUCCESS = "success"
    STATUS_FAILURE = "failure"
    STATUS_INFO = "info"

    STATUS_CHOICES = [
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILURE, "Failure"),
        (STATUS_INFO, "Info"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    # Snapshot so the record survives user deletion or email changes
    user_email = models.EmailField(blank=True)
    action = models.CharField(max_length=40, choices=ACTION_CHOICES, db_index=True)
    action_category = models.CharField(
        max_length=20, choices=CATEGORY_CHOICES, blank=True, db_index=True
    )
    module = models.CharField(max_length=64, blank=True, db_index=True)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    target_display = models.CharField(max_length=255, blank=True)
    details = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_SUCCESS, db_index=True
    )
    error_message = models.TextField(blank=True)
    path = models.TextField(blank=True)
    method = models.CharField(max_length=10, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    session_key = models.CharField(max_length=64, blank=True)
    extra = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["user", "-timestamp"]),
            models.Index(fields=["action", "-timestamp"]),
            models.Index(fields=["action_category", "-timestamp"]),
            models.Index(fields=["status", "-timestamp"]),
            models.Index(fields=["module", "-timestamp"]),
        ]
        verbose_name = "User Activity"
        verbose_name_plural = "User Activities"

    @property
    def category_label(self):
        category = self.action_category or self.CATEGORY_FOR_ACTION.get(self.action, "")
        return dict(self.CATEGORY_CHOICES).get(category, "")

    @property
    def display_label(self):
        """Readable ``"User > Log in"`` style label for the audit table."""
        action_label = self.get_action_display()
        category = self.category_label
        return f"{category} › {action_label}" if category else action_label

    @classmethod
    def log(
        cls,
        action,
        *,
        user=None,
        request=None,
        extra=None,
        action_category=None,
        module="",
        target_type="",
        target_id="",
        target_display="",
        details="",
        status=None,
        error_message="",
    ):
        """Record an audit entry. Never raises — failures are logged, not propagated."""
        from va_explorer.utils.request_helpers import get_client_ip

        user_email = ""
        ip_address = None
        user_agent = ""
        session_key = ""
        path = ""
        method = ""

        if user and hasattr(user, "email"):
            user_email = user.email or ""
        if request is not None:
            ip_address = get_client_ip(request) or None
            user_agent = request.headers.get("user-agent", "")[:512]
            session_key = (request.session.session_key or "")[:64]
            path = request.path or ""
            method = request.method or ""
            if (
                not user_email
                and hasattr(request, "user")
                and hasattr(request.user, "email")
            ):
                user_email = request.user.email or ""

        category = action_category or cls.CATEGORY_FOR_ACTION.get(action, "")

        try:
            cls.objects.create(
                user=user,
                user_email=user_email,
                action=action,
                action_category=category,
                module=(module or "")[:64],
                target_type=(target_type or "")[:64],
                target_id=(str(target_id) if target_id is not None else "")[:64],
                target_display=(target_display or "")[:255],
                details=details or "",
                status=status or cls.STATUS_SUCCESS,
                error_message=error_message or "",
                path=path,
                method=method,
                ip_address=ip_address,
                user_agent=user_agent,
                session_key=session_key,
                extra=extra or {},
            )
        except Exception:
            # Audit logging must never break the main user workflow, but the
            # failure should still be visible in the application logs.
            logger.exception(
                "Failed to write UserActivity audit entry for action=%s", action
            )


@receiver(post_save, sender=User)
def assign_worker_id_to_mortality_officer(sender, instance, created, **kwargs):
    """
    Post-save signal to assign a unique worker_id to users in the
    'Mortality Surveillance Officer' group.
    """
    # Check if user is in "Mortality Surveillance Officer" group
    is_mortality_officer = instance.groups.filter(
        name="Mortality Surveillance Officer"
    ).exists()

    # Only assign worker_id if:
    # 1. User is a Mortality Surveillance Officer
    # 2. User doesn't already have a worker_id
    if is_mortality_officer and not instance.worker_id:
        try:
            # Generate unique worker_id
            worker_id = generate_unique_worker_id()

            # Assign to user and save (using update to avoid triggering signal again)
            User.objects.filter(pk=instance.pk).update(worker_id=worker_id)

            # Update the instance in memory
            instance.worker_id = worker_id
        except ValueError as e:
            # Log error if all worker IDs are exhausted
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                "Failed to generate worker_id for user %s: %s", instance.email, e
            )
