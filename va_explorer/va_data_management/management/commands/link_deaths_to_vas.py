"""Retrospective Death -> VA linking command (formerly ``link_verbal_autopsies``).

Dry-run by default; ``--apply`` is required to persist. Uses the same reusable
matching service as ingestion, so behaviour is identical everywhere.

``--calibrate`` produces the report-only calibration output required by the
linkage-hardening spec: per-method link counts, review-queue sizes, per-block
candidate statistics, per-tool breakdown, same-tool duplicates, demoted
previous links (v1-vs-v2 diff) and the achieved link rate measured against
the ≥75% prior (``VA_DEATH_MATCH_EXPECTED_LINK_RATE``).
"""

import csv
import json
import statistics
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from va_explorer.va_data_management.models import VASA, VerbalAutopsy
from va_explorer.va_data_management.utils.matching import run_matching

_MODELS = {"va": VerbalAutopsy, "vasa": VASA}

_REPORT_FIELDS = [
    "va_model",
    "va_pk",
    "va_identifier",
    "va_tool",
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
    "block_sources",
    "review_reason",
    "review_mode",
    "config_version",
    "crosswalk_version",
    "changed",
    "previously_linked",
    "previous_was_manual",
    "demoted_previous_link",
    "source",
    "algorithm_version",
    "error",
]


class Command(BaseCommand):
    help = (
        "Evaluate and (with --apply) link Verbal Autopsies to Death records "
        "using the shared matching service. Dry-run by default; --calibrate "
        "for the report-only calibration summary."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist links. Omit for a dry run (no DB writes).",
        )
        parser.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            help="Explicit dry run (the default; spec-named alias).",
        )
        parser.add_argument(
            "--model",
            choices=["va", "vasa", "both"],
            default="both",
            help="Which VA model(s) to process (default: both).",
        )
        parser.add_argument(
            "--tool",
            choices=["community", "facility", "vasa", "all"],
            default=None,
            help="Filter by tool: community/facility VA, VASA, or all "
            "(overrides --model).",
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
            "--since",
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
            "--rebuild",
            dest="recheck",
            action="store_true",
            help="Re-evaluate even manually-confirmed links (authorised review).",
        )
        parser.add_argument(
            "--reconcile-v1",
            dest="reconcile_v1",
            action="store_true",
            help="Only rows last evaluated by the v1 algorithm (migration pass).",
        )
        parser.add_argument(
            "--calibrate",
            action="store_true",
            help="Report-only calibration run (implies dry run; rejects --apply).",
        )
        parser.add_argument(
            "--config",
            dest="config_name",
            default=None,
            help="MatchingConfig row to evaluate under (default: the active one).",
        )
        parser.add_argument(
            "--export-report",
            dest="export_report",
            default=None,
            help="Write a CSV (+ .json) report to this path.",
        )
        parser.add_argument("--source", default="retrospective_command")
        parser.add_argument("--batch-size", dest="batch_size", type=int, default=500)
        parser.add_argument(
            "--mode",
            choices=["shadow", "review_only", "auto"],
            default=None,
            help="Override decision mode. Shadow never writes; review_only "
            "persists candidates but asserts no automatic links.",
        )

    def _build_queryset(self, model, opts):
        qs = model.objects.all()
        if opts["va_id"]:
            va_id = opts["va_id"]
            if va_id.isdigit():
                qs = qs.filter(pk=int(va_id))
            else:
                qs = qs.filter(Q(instanceid=va_id) | Q(key=va_id))
        if opts["tool"] and model is VerbalAutopsy:
            # community_va is normalised to yes/no on save.
            if opts["tool"] == "community":
                qs = qs.filter(community_va__iexact="yes")
            elif opts["tool"] == "facility":
                qs = qs.exclude(community_va__iexact="yes")
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
        if opts["reconcile_v1"]:
            # v1-evaluated rows (and their history) — the legacy-migration pass.
            qs = qs.filter(
                Q(va_death_match__algorithm_version__startswith="1")
                | Q(va_death_match__algorithm_version__isnull=True),
                va_death_match__isnull=False,
            )

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

    def _models_for(self, opts):
        if opts["tool"]:
            if opts["tool"] == "vasa":
                return [VASA]
            if opts["tool"] in {"community", "facility"}:
                return [VerbalAutopsy]
            return [VerbalAutopsy, VASA]
        if opts["model"] == "both":
            return [VerbalAutopsy, VASA]
        return [_MODELS[opts["model"]]]

    def _iter_vas(self, opts):
        for model in self._models_for(opts):
            qs = self._build_queryset(model, opts)
            # iterator() streams; chunk_size keeps prefetch working in batches.
            yield from qs.iterator(chunk_size=opts["batch_size"])

    def handle(self, *args, **opts):
        if opts["calibrate"] and opts["apply"]:
            raise CommandError("--calibrate is report-only; drop --apply.")
        if opts["dry_run"] and opts["apply"]:
            raise CommandError("--dry-run and --apply are mutually exclusive.")
        if opts["batch_size"] < 1:
            raise CommandError("--batch-size must be positive.")
        if opts["limit"] is not None and opts["limit"] < 1:
            raise CommandError("--limit must be positive.")
        if opts["mode"] == "shadow" and opts["apply"]:
            raise CommandError("--mode shadow cannot be combined with --apply.")
        apply = opts["apply"] and opts["mode"] != "shadow"
        review_only = opts["mode"] == "review_only"
        rows = []

        def collect(result):
            rows.append(result.to_report_row())

        self.stdout.write(
            self.style.WARNING("DRY RUN — no changes will be written.")
            if not apply
            else self.style.SUCCESS("APPLY — links will be persisted.")
        )

        try:
            summary = run_matching(
                self._iter_vas(opts),
                apply=apply,
                force_recheck=opts["recheck"],
                source=opts["source"],
                on_result=(
                    collect if opts["calibrate"] or opts["export_report"] else None
                ),
                config_name=opts["config_name"],
                review_only=review_only,
            )
        except (ImproperlyConfigured, ObjectDoesNotExist) as exc:
            raise CommandError(str(exc)) from exc

        self._print_summary(summary)
        if opts["calibrate"]:
            self._print_calibration(summary, rows)

        if opts["export_report"]:
            self._write_report(opts["export_report"], rows, summary)

        if summary.get("failed", 0):
            raise CommandError(
                f"{summary['failed']} record(s) failed matching — see log."
            )

    def _print_summary(self, summary):
        self.stdout.write("")
        self.stdout.write(f"Config:               {summary.get('config')}")
        self.stdout.write(f"Deaths indexed:       {summary.get('deaths_indexed', 0)}")
        self.stdout.write(f"Total evaluated:      {summary['total']}")
        self.stdout.write(f"  matched (auto):     {summary.get('matched_auto', 0)}")
        self.stdout.write(f"  matched (manual):   {summary.get('matched_manual', 0)}")
        self.stdout.write(f"  pending review:     {summary.get('pending_review', 0)}")
        self.stdout.write(f"  ambiguous:          {summary.get('ambiguous', 0)}")
        self.stdout.write(f"  unmatched:          {summary.get('unmatched', 0)}")
        self.stdout.write(f"  skipped (manual):   {summary.get('skipped', 0)}")
        self.stdout.write(f"  already linked:     {summary.get('already_linked', 0)}")
        self.stdout.write(
            f"  demoted prev links: {summary.get('demoted_previous_link', 0)}"
        )
        self.stdout.write(f"  errors:             {summary.get('failed', 0)}")
        self.stdout.write(f"  changed (writes):   {summary.get('changed', 0)}")
        methods = summary.get("methods") or {}
        if methods:
            self.stdout.write("Per-method:")
            for method, count in sorted(methods.items(), key=lambda kv: -kv[1]):
                self.stdout.write(f"  {method:22s} {count}")

    # ------------------------------------------------------------ calibration
    def _print_calibration(self, summary, rows):
        total = summary["total"] or 1
        linked = summary.get("matched_auto", 0) + summary.get("matched_manual", 0)
        queued = summary.get("pending_review", 0) + summary.get("ambiguous", 0)
        # Skipped rows are existing manual links: they count as linked coverage.
        linked += summary.get("skipped", 0)
        link_rate = 100.0 * linked / total
        coverage = 100.0 * (linked + queued) / total
        prior = float(getattr(settings, "VA_DEATH_MATCH_EXPECTED_LINK_RATE", 75))

        self.stdout.write("")
        self.stdout.write("=== Calibration (vs expected link-rate prior) ===")
        self.stdout.write(f"Linked (auto+manual):          {linked} ({link_rate:.1f}%)")
        self.stdout.write(f"Review queue (pending+ambig):  {queued}")
        self.stdout.write(
            f"Candidate coverage:            {coverage:.1f}% "
            f"(linked + review-queued likely)"
        )
        self.stdout.write(f"Expected prior:                >= {prior:.0f}%")
        verdict = (
            self.style.SUCCESS("PASS — confirmed linkage rate meets the prior.")
            if link_rate >= prior
            else self.style.ERROR(
                "INVESTIGATE — confirmed linkage rate is below the prior. "
                "Candidate coverage is diagnostic only; queued records are not links."
            )
        )
        self.stdout.write(verdict)

        # Per-tool breakdown.
        by_tool: dict = {}
        for row in rows:
            tool = row.get("va_tool") or row["va_model"]
            bucket = by_tool.setdefault(tool, {"total": 0, "linked": 0, "queued": 0})
            bucket["total"] += 1
            if row["status"] in {"matched_auto", "matched_manual", "skipped"}:
                bucket["linked"] += 1
            elif row["status"] in {"pending_review", "ambiguous"}:
                bucket["queued"] += 1
        self.stdout.write("Per-tool:")
        for tool, b in sorted(by_tool.items()):
            linked_pct = 100.0 * b["linked"] / (b["total"] or 1)
            coverage_pct = 100.0 * (b["linked"] + b["queued"]) / (b["total"] or 1)
            self.stdout.write(
                f"  {tool:14s} total={b['total']:<6} linked={b['linked']:<6} "
                f"queued={b['queued']:<6} linked_rate={linked_pct:.1f}% "
                f"coverage={coverage_pct:.1f}%"
            )

        # Per-block candidate statistics.
        block_stats = summary.get("block_stats") or {}
        if block_stats:
            self.stdout.write("Per-block candidate counts (min/median/p95/max):")
            for block, counts in sorted(block_stats.items()):
                if block in {"total", "truncated"} or not counts:
                    continue
                counts_sorted = sorted(counts)
                p95_index = min(len(counts_sorted) - 1, int(0.95 * len(counts_sorted)))
                self.stdout.write(
                    f"  {block:28s} n={len(counts):<6} min={counts_sorted[0]:<5} "
                    f"med={statistics.median(counts_sorted):<7.1f} "
                    f"p95={counts_sorted[p95_index]:<5} max={counts_sorted[-1]}"
                )

        # Contradiction-guard triggers (deterministic key demographics conflict).
        guard_hits = [
            row
            for row in rows
            if row["status"] == "pending_review"
            and row["method"]
            and row["method"] != "probabilistic"
            and not row["demoted_previous_link"]
        ]
        self.stdout.write(f"Contradiction-guard reviews:   {len(guard_hits)}")

        # v1-vs-v2 diff: every previous link the new evaluation disagrees with.
        demoted = [row for row in rows if row["demoted_previous_link"]]
        self.stdout.write(f"Demoted previous links:        {len(demoted)}")
        for row in demoted[:50]:
            self.stdout.write(
                f"  {row['va_model']}#{row['va_pk']}: now proposes "
                f"{row['death_id'] or row['death_pk'] or 'nothing'} "
                f"(score {row['score']}) — previous link kept in alternatives"
            )
        if len(demoted) > 50:
            self.stdout.write(f"  ... and {len(demoted) - 50} more (see report export)")

        # Same-tool duplicates: one death auto-linked by >1 record of one tool.
        seen: dict = {}
        duplicates = []
        for row in rows:
            if row["status"] not in {"matched_auto", "matched_manual"}:
                continue
            if not row["death_pk"]:
                continue
            key = (row.get("va_tool") or row["va_model"], row["death_pk"])
            if key in seen:
                duplicates.append((key, seen[key], row["va_pk"]))
            else:
                seen[key] = row["va_pk"]
        self.stdout.write(f"Same-tool duplicate links:     {len(duplicates)}")
        for (tool, death_pk), first_pk, second_pk in duplicates[:20]:
            self.stdout.write(
                f"  death#{death_pk} linked by {tool}#{first_pk} and #{second_pk}"
            )

        for method in ("name_nrc_exact", "name_nrc_probabilistic"):
            method_rows = [row for row in rows if row["method"] == method]
            auto_count = sum(row["status"] == "matched_auto" for row in method_rows)
            review_count = sum(
                row["status"] in {"pending_review", "ambiguous"} for row in method_rows
            )
            self.stdout.write(
                f"{method}: total={len(method_rows)} auto={auto_count} "
                f"review={review_count}"
            )

    def _write_report(self, path, rows, summary):
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
                {
                    "generated_at": timezone.now().isoformat(),
                    "summary": summary,
                    "results": rows,
                },
                f,
                indent=2,
                default=str,
            )
        self.stdout.write(f"Report written: {out} and {json_path}")
