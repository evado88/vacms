# VA → Death record linkage — Phase 1 implementation

Implements the core of `docs/death_va_linkage.md`: a safe, auditable, reusable
service that links each Verbal Autopsy to its Death record only when confidence
is high, and never forces a match. **An unmatched VA is preferable to a
wrongly-linked one.**

## What shipped (Phase 1)

- A reusable matching engine (`va_data_management/utils/matching/`).
- A dedicated audit model `VADeathMatch` (+ migration `0049`).
- A retrospective management command `link_verbal_autopsies` (dry-run default).
- A Celery sweeper + flag-gated ingestion hooks (auto-linking **OFF** by default).
- Read-mostly Django admin for reviewing matches.
- 19 tests; verified against production-like dev data.

**Not in Phase 1** (future): a dedicated manual-review web UI; enabling
auto-linking in production; re-evaluating unmatched VAs automatically when a new
Death arrives; a VA-side district/province code→name resolver (only ward is
resolved today — see Limitations).

## Data model reality (why the design is shaped this way)

There are **two** VA models with different bridges to Death:

| | Exact key to Death | Fuzzy fallback |
|---|---|---|
| `VASA` | `VASASocialAutopsy.DeathID` ↔ `Death.death_id` (already validated by `death_ids.validate_vasa_death_ids`) | yes |
| `VerbalAutopsy` | CMS `Event` (`event.va` + `event.death` on one row) | yes |

There is **no** direct VA↔Death FK in the schema; the link now lives only on
`VADeathMatch`. `VerbalAutopsy` stores geography as **numeric codes**
(`ward=502108`) while `Death` stores **names** (`ward=KASENGA`); the engine
resolves ward codes via `ClusterLocationCodes` before comparing.

### Field crosswalk (identity matching)

| Concept | VA / VASA (WHO) | Death | Weight |
|---|---|---|---|
| Name | `Id10017` + `Id10018` | `DE_03` | 30 |
| Date of death | `Id10023` | `DE_06` | 27 |
| Date of birth | `Id10021` | `DE_04` | 18 |
| Sex | `Id10019` | `DE_05` | 12 |
| EA (enumeration area) | `ea` (digits) | `ea` (`EA_0<digits>`) | 10 |
| Ward | `ward` (code→name) | `ward` | 8 |
| District | `district` | `district` | 3 |
| Place of death | `Id10058` | `DE_07` | 2 |

EA is the finest geographic unit and a strong locator. VA stores bare digits
(`5021031018`); Death stores a prefixed form (`EA_05021031018`). Both are reduced
to digits (leading zero stripped) before comparison — an exact match awards full
weight, a mismatch is a soft contradiction. EA is also the narrowest candidate
**blocking** key (`Death.ea__icontains=<va-digits>`), tried before ward.

## Scoring & classification

Score = agreement over **comparable (both-present)** fields only — missing
values never add positive evidence. Each field is classified exact / normalized /
fuzzy / compatible / contradictory / missing. Names use `fuzzywuzzy`
token-set ratio; dates use tolerances (config).

**Hard disqualifiers** (cap the score below the review band so they can never
auto-link): different sex, date-of-death > 30 days apart, or name similarity
< 40. A **coverage guard** requires the name plus one other strong identifier to
agree before a score can exceed the review band (so a single field can't
auto-link).

Outcomes (thresholds configurable):

| Condition | Status |
|---|---|
| exact key, no hard contradiction | `MATCHED_AUTO` (score 100) |
| exact key **with** hard contradiction | `PENDING_REVIEW` |
| fuzzy score > 90, separation ≥ min, no hard contradiction | `MATCHED_AUTO` |
| fuzzy score > 90 but runner-up too close | `AMBIGUOUS` |
| 80 ≤ fuzzy score ≤ 90 | `PENDING_REVIEW` |
| fuzzy score < 80 | `UNMATCHED` |
| no candidate | `UNMATCHED` |

Only `MATCHED_AUTO` / `MATCHED_MANUAL` set `va.linked_death`.

## Configuration (`config/settings/base.py`, all `env`-overridable)

| Setting | Default | Meaning |
|---|---|---|
| `VA_DEATH_MATCH_ENABLED` | `False` | Master switch for auto-linking during ingestion |
| `VA_DEATH_MATCH_AUTO_THRESHOLD` | `90` | Auto-link requires score strictly above this |
| `VA_DEATH_MATCH_REVIEW_THRESHOLD` | `80` | Lower bound of the manual-review band |
| `VA_DEATH_MATCH_MIN_SEPARATION` | `5` | Min gap to the runner-up for an auto-link |
| `VA_DEATH_MATCH_MAX_DOD_TOLERANCE_DAYS` | `3` | "Compatible" date-of-death window |
| `VA_DEATH_MATCH_CANDIDATE_DOD_WINDOW_DAYS` | `14` | Blocking date window |
| `VA_DEATH_MATCH_MAX_CANDIDATES` | `50` | Max candidates scored per VA |
| `VA_DEATH_MATCH_ALGORITHM_VERSION` | `"1.0"` | Recorded on every match |
| `VA_DEATH_MATCH_RAISE_INGESTION_ERRORS` | `False` | If False, matcher errors never break ingestion |

## Reusable API

```python
from va_explorer.va_data_management.utils.matching import (
    evaluate_va,                        # pure, no writes -> MatchResult
    auto_link_verbal_autopsy_to_death,  # evaluate + persist (transaction-safe)
    run_matching,                       # batched driver -> summary dict
    schedule_link_for_va_ids,           # ingestion seam (no-op unless flag on)
)
```

`auto_link_verbal_autopsy_to_death` skips manually-confirmed links (unless
`force_recheck`), is idempotent (an unchanged re-evaluation writes no history
row — the timestamp is bumped via `UPDATE`), and never raises into ingestion.

## Ingestion pathways (all flag-gated, OFF by default)

The single seam `schedule_link_for_va_ids` (which no-ops unless the flag is on
and enqueues after `transaction.on_commit`) is wired at the two loaders every
pathway funnels through:

- `utils/loading.py::load_records_from_dataframe` → covers PyODK VA, CSV, Kobo.
- `utils/vasa_loading.py::load_vasa_from_dataframes` → covers VASA.

Plus the sweeper `tasks.sweep_unlinked_vas` (mirrors `run_coding_algorithms`;
scheduled only when the flag is on) for anything missed.

## Operations

```bash
# 1. Dry run (default; no writes). Review the report before applying.
python manage.py link_verbal_autopsies --model both --export-report reports/link.csv

# 2. Apply, in a bounded slice first.
python manage.py link_verbal_autopsies --model both --limit 500 --apply

# Useful filters: --va-id --district --facility --from-date --to-date
#                 --status <existing-status> --limit --recheck --source
```

The command prints counts (matched/review/ambiguous/unmatched/skipped/already/
errors/changed) and writes CSV + JSON reports.

## Deployment

1. Deploy code + run migration `0049` (adds `VADeathMatch` + its history table;
   **no changes to VA/Death tables**).
2. Leave `VA_DEATH_MATCH_ENABLED=False`. Operate via the retrospective command.
3. When ready to auto-link on ingest, set `VA_DEATH_MATCH_ENABLED=True` and
   restart workers (registers the hourly sweeper; ingestion hooks activate).

## Rollback

- Auto-links are fully reversible: the link lives only on `VADeathMatch`;
  deleting/clearing its rows removes all links (nothing is written onto VA or
  Death rows). `VADeathMatch.history` retains the audit trail.
- Set `VA_DEATH_MATCH_ENABLED=False` to instantly disable all auto-linking.
- Reverting migration `0049` drops the match tables and is non-destructive to
  existing data.

## Monitoring

- Watch the status distribution over time (admin list filters, or the command
  summary): a spike in `AMBIGUOUS`/`PENDING_REVIEW` suggests threshold tuning;
  any `ERROR` rows warrant a log check.
- Sample-audit a handful of `MATCHED_AUTO` rows after each large run.

## Limitations / future work

- **Geography codes:** only the VA *ward* code is resolved to a name (via
  `ClusterLocationCodes`); VA district/province remain coded and therefore score
  as contradictory against the named Death register (soft, non-disqualifying).
  A fuller code→name resolver would improve district/province signal.
- **Manual-review UI:** admin only for now; a dedicated queue/workflow is the
  recommended next phase (confirm / reject / reassign, writing `MATCHED_MANUAL`
  / `REJECTED` with `confirmed_by`).
- **Reactive re-evaluation:** a Death created after an unmatched VA is not yet
  auto-re-evaluated; the sweeper will pick it up on its next pass.

## Verified on dev data

A dry run over 2,665 VerbalAutopsies produced 313 auto-matches, 95 review, 3
ambiguous, 2,254 unmatched, 0 errors, 0 writes (EA scoring/blocking pulled ~40
plausible records out of unmatched into the review band). Spot-check example — VA
"Beene Kamwaya" (F, DOB 1983-07-17, DOD 2025-08-10, ward code 502108, EA
5021031018) auto-linked (score 95.45) to Death "Beene Kamwaya" (FEMALE, same DOB,
same DOD, ward KASENGA, EA EA_05021031018): exact name, DOB, DOD, sex, EA, and
ward (code resolved to the matching name).
