"""Shared model behaviour for ODK-sourced forms."""

import logging

from django.core.exceptions import ValidationError
from django.db import models

from ..utils.submission_timestamp import parse_form_date, parse_submission_timestamp

logger = logging.getLogger(__name__)

#: Default derivation for forms whose ``submissiondate`` is always populated.
SUBMISSIONDATE_ONLY = (("submissiondate", parse_submission_timestamp),)

#: Derivation for forms where ``submissiondate`` has gaps and the form's own
#: ``today`` column is the only record of when the submission happened.
SUBMISSIONDATE_THEN_TODAY = (
    ("submissiondate", parse_submission_timestamp),
    ("today", parse_form_date),
)


def derive_submitted_at(instance, sources):
    """Return the canonical timestamp for ``instance`` from its raw columns.

    ``sources`` is an ordered sequence of ``(field_name, parser)`` pairs; the
    first field that is both present and parseable wins. Returns ``None`` when
    every source is blank.

    Raises ``ValidationError`` when no source produced a value but at least one
    held a malformed one — a bad value must stay visible rather than be silently
    downgraded to "no timestamp".
    """
    error = None
    for field_name, parser in sources:
        try:
            parsed = parser(getattr(instance, field_name, None))
        except ValidationError as exc:
            error = error or exc
            continue
        if parsed is not None:
            return parsed

    if error is not None:
        raise error
    return None


class SubmissionTimestampMixin(models.Model):
    """Adds a canonical, filterable submission timestamp to an ODK form model.

    Exports filter this derived column rather than the raw text ones: the raw
    values arrive in several shapes and are nullable, so comparing them directly
    silently drops records from dated downloads.
    """

    #: Ordered ``(field_name, parser)`` pairs consulted to derive
    #: ``submitted_at``. Override on a subclass to declare a fallback column.
    SUBMISSION_TIMESTAMP_SOURCES = SUBMISSIONDATE_ONLY

    submitted_at = models.DateTimeField(
        "Canonical submission timestamp", null=True, blank=True, editable=False
    )

    class Meta:
        abstract = True

    def synchronize_submitted_at(self):
        """Refresh the canonical timestamp derived from raw ODK provenance.

        Raises ``ValidationError`` for a malformed non-blank raw value. Ingestion
        callers catch it and flag the record so the bad value stays visible and
        remediable; ``save()`` deliberately does not, see below.
        """
        self.submitted_at = derive_submitted_at(self, self.SUBMISSION_TIMESTAMP_SOURCES)
        return self.submitted_at

    def save(self, *args, **kwargs):
        # ``update_fields`` is Model.save()'s 4th positional parameter; collapse
        # it into kwargs so there is only one form to reason about below.
        if len(args) >= 4:
            kwargs["update_fields"] = args[3]
            args = args[:3]
        update_fields = kwargs.get("update_fields")

        source_fields = {name for name, _parser in self.SUBMISSION_TIMESTAMP_SOURCES}

        # Only re-derive when this save actually writes one of the raw values. An
        # unrelated write must not touch the canonical column: soft deletion is
        # implemented as ``save()`` (va_explorer/models.py), so re-deriving
        # unconditionally would let one bad legacy value block deletes, edits,
        # re-coding, and geocoding for that record forever.
        if update_fields is None or source_fields & set(update_fields):
            try:
                self.synchronize_submitted_at()
            except ValidationError:
                # ``submitted_at`` is purely derived from the raw columns. If
                # they are unparseable the derived value is unknown, and
                # retaining a stale timestamp would make exports misreport when
                # this record arrived. Null it and leave a trace instead.
                logger.warning(
                    "%s %s: unparseable submission date in %s; submitted_at cleared",
                    type(self).__name__,
                    self.pk,
                    ", ".join(
                        f"{name}={getattr(self, name, None)!r}"
                        for name in sorted(source_fields)
                    ),
                )
                self.submitted_at = None
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"submitted_at"}

        super().save(*args, **kwargs)
