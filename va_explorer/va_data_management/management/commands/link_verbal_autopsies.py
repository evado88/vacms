"""Retrospective VA -> Death linking command.

Dry-run by default; ``--apply`` is required to persist links. Uses the same
reusable matching service as ingestion, so behaviour is identical everywhere.
"""

import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from va_explorer.va_data_management.models import VASA, VerbalAutopsy
from va_explorer.va_data_management.utils.matching import run_matching

_MODELS = {"va": VerbalAutopsy, "vasa": VASA}

_REPORT_FIELDS = [
    "va_model",
    "va_pk",
    "va_identifier",
    "death_pk",
    "death_id",
    "score",
    "score_separation",
    "method",
    "status",
    "supporting_fields",
    "contradictory_fields",
    "missing_fields",
    "alternatives",
    "changed",
    "previously_linked",
    "previous_was_manual",
    "source",
    "algorithm_version",
    "error",
]


class Command(BaseCommand):
    help = (
        "Evaluate and (with --apply) link Verbal Autopsies to Death records "
        "using the shared matching service. Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist links. Omit for a dry run (no DB writes).",
        )
        parser.add_argument(
            "--model",
            choices=["va", "vasa", "both"],
            default="both",
            help="Which VA model(s) to process (default: both).",
        )
        parser.add_argument(
            "--va-id",
            dest="va_id",
            default=None,
            help="Limit to one VA by pk or instanceid/key.",
        )
        parser.add_argument("--district", default=None)
        parser.add_argument(
            "--facility",
            default=None,
            help="Substring match on the VA hospital/facility.",
        )
        parser.add_argument(
            "--from-date",
            dest="from_date",
            default=None,
            help="Only VAs created on/after this date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--to-date",
            dest="to_date",
            default=None,
            help="Only VAs created on/before this date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--status",
            default=None,
            help="Only VAs whose existing match has this status.",
        )
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument(
            "--recheck",
            action="store_true",
            help="Re-evaluate even manually-confirmed links (authorised review).",
        )
        parser.add_argument(
            "--export-report",
            dest="export_report",
            default=None,
            help="Write a CSV (+ .json) report to this path.",
        )
        parser.add_argument("--source", default="retrospective_command")
        parser.add_argument("--batch-size", dest="batch_size", type=int, default=500)

    def _build_queryset(self, model, opts):
        qs = model.objects.all()
        if opts["va_id"]:
            va_id = opts["va_id"]
            if va_id.isdigit():
                qs = qs.filter(pk=int(va_id))
            else:
                qs = qs.filter(instanceid=va_id)
        if opts["district"]:
            qs = qs.filter(district__iexact=opts["district"])
        if opts["facility"]:
            qs = qs.filter(hospital__icontains=opts["facility"])
        if opts["from_date"]:
            qs = qs.filter(created__date__gte=opts["from_date"])
        if opts["to_date"]:
            qs = qs.filter(created__date__lte=opts["to_date"])
        if opts["status"]:
            qs = qs.filter(va_death_match__status=opts["status"])

        # Prefetch the relations the matcher touches to avoid N+1 queries.
        qs = qs.select_related("va_death_match")
        if model is VerbalAutopsy:
            qs = qs.prefetch_related("events__death")
        else:
            qs = qs.select_related("social_autopsy")
        qs = qs.order_by("pk")
        if opts["limit"]:
            qs = qs[: opts["limit"]]
        return qs

    def _iter_vas(self, opts):
        models = (
            [VerbalAutopsy, VASA]
            if opts["model"] == "both"
            else [_MODELS[opts["model"]]]
        )
        for model in models:
            qs = self._build_queryset(model, opts)
            # iterator() streams; chunk_size keeps prefetch working in batches.
            yield from qs.iterator(chunk_size=opts["batch_size"])

    def handle(self, *args, **opts):
        apply = opts["apply"]
        rows = []

        def collect(result):
            rows.append(result.to_report_row())

        self.stdout.write(
            self.style.WARNING("DRY RUN — no changes will be written.")
            if not apply
            else self.style.SUCCESS("APPLY — links will be persisted.")
        )

        summary = run_matching(
            self._iter_vas(opts),
            apply=apply,
            force_recheck=opts["recheck"],
            source=opts["source"],
            on_result=collect,
        )

        self._print_summary(summary)

        if opts["export_report"]:
            self._write_report(opts["export_report"], rows)

        if summary.get("failed", 0):
            raise CommandError(
                f"{summary['failed']} record(s) failed matching — see log."
            )

    def _print_summary(self, summary):
        self.stdout.write("")
        self.stdout.write(f"Total evaluated:      {summary['total']}")
        self.stdout.write(f"  matched (auto):     {summary.get('matched_auto', 0)}")
        self.stdout.write(f"  matched (manual):   {summary.get('matched_manual', 0)}")
        self.stdout.write(f"  pending review:     {summary.get('pending_review', 0)}")
        self.stdout.write(f"  ambiguous:          {summary.get('ambiguous', 0)}")
        self.stdout.write(f"  unmatched:          {summary.get('unmatched', 0)}")
        self.stdout.write(f"  skipped (manual):   {summary.get('skipped', 0)}")
        self.stdout.write(f"  already linked:     {summary.get('already_linked', 0)}")
        self.stdout.write(f"  errors:             {summary.get('failed', 0)}")
        self.stdout.write(f"  changed (writes):   {summary.get('changed', 0)}")

    def _write_report(self, path, rows):
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_REPORT_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        json_path = out.with_suffix(".json")
        with json_path.open("w") as f:
            json.dump(
                {"generated_at": timezone.now().isoformat(), "results": rows},
                f,
                indent=2,
                default=str,
            )
        self.stdout.write(f"Report written: {out} and {json_path}")
