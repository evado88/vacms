# Death<->VA Linkage Hardening: Audit and Remaining Plan

Last audited: 2026-07-17 against commit `4cdfe46` and the local database.

## Implementation Update

Implemented on 2026-07-17 after the baseline audit below:

- Added migration `0054_vadeathmatchdecision_and_more` with review routing,
  reproducible config/crosswalk snapshots, protected error state, optimistic
  decision versions, matched-link invariants, and immutable human decision
  events.
- Made manual links authoritative under every matcher flag. Added locked,
  location-scoped confirm/reject/unlink/reassign services, alternative rescoring,
  durable rejection/decision history, read-only linkage admin, and safe local
  redirects.
- Replaced NRC-only linking with `name_nrc_exact` and
  `name_nrc_probabilistic`. Exact NRC plus complete exact name auto-links one
  unique Death. Fuzzy name auto-linking is thresholded per overall/given/surname
  component and remains disabled by default until the labelled precision gate
  passes. NRC/name conflicts and duplicate NRCs route to in-person review.
- Removed sex pruning, added non-unique identifier blocks, fixed community
  district and unknown-tool handling, made single/batch indexing logically
  identical, made phonetic blocks name-order independent, and replaced first-PK
  truncation with deterministic ranking plus retained/dropped metrics.
- Changed heuristic scoring to a fixed core denominator with capped supporting
  evidence, explicit contradiction penalties, one-to-one name alignment, and
  separate month/year date precision. The score is still a heuristic, not a
  probability.
- Versioned evaluations with full effective config and crosswalk snapshots,
  validated unknown/invalid config keys, protected referenced config revisions,
  and separated authoritative link rate from diagnostic candidate coverage.
- Tracked the generated CSV, bound crosswalk versions to forms/rules/VASA
  mapping/facility header, added group paths and fail-closed source validation,
  and added both artifact drift checks to pre-commit.
- Added `off`, `shadow`, `review_only`, and `auto` operating modes, stale/revision
  re-evaluation, apply-mode block metrics, safer command validation, and expanded
  review filters.
- Added focused regression coverage for NRC/name decisions, sparse/conflicting
  evidence, manual/error preservation, location scope, stale decisions,
  alternative evidence, rejection, unlinking, config revision safety, command
  modes, and crosswalk drift.

The following are rollout gates rather than code-complete claims:

- Obtain and approve an authoritative facility-VA XLSForm and the
  facility/area/hospital-to-district mapping.
- Build the dual-reviewed, adjudicated, de-identified calibration/holdout set;
  select tool-specific thresholds and enable probabilistic NRC-name auto-linking
  only after its independent precision gate passes.
- Export the production v1 baseline, resolve existing demotions/duplicates, and
  execute shadow, review-only, canary, reconciliation, and auto rollout in that
  order.
- Profile normalized database indexes or a revisioned Death-index cache at 10x
  volume before replacing the correctness-first full single-record index.

The remaining sections preserve the pre-implementation audit evidence and the
rollout gates against which this implementation must be validated.

## Purpose

This document is the implementation plan for
`docs/death_va_linkage_hardenning.md`. The first v2 implementation is already in
the repository, so this is no longer a greenfield phase list. It records what is
actually implemented, identifies gaps against the requirement, and orders the
remaining work by data-integrity risk.

The following prior decisions remain in force:

- Keep `VADeathMatch` as the authoritative link model; do not introduce a second
  authoritative `RecordLink` model.
- Keep `jellyfish` for Jaro-Winkler and phonetic helpers.
- Keep the command name `link_deaths_to_vas`.
- A VA/VASA may link to at most one Death. A Death may link to more than one
  VA-type record.
- Use exact NRC equality as a strong candidate block, then evaluate the deceased
  name with dedicated name thresholds. Auto-link one unique, non-rejected Death
  when the valid normalized NRC matches and the name is either exact or clears
  the calibrated NRC-name auto threshold with no component-level name conflict.
  Record exact and fuzzy outcomes as `name_nrc_exact` and
  `name_nrc_probabilistic`. NRC matches with a materially different given name
  (for example, the same surname but a different first name), duplicate Death
  candidates, or hard sex/DOD-year contradictions require in-person review.

Supporting candidate/decision tables may be added if needed, but they must not
become a competing source of truth for the authoritative link.

## Baseline Current State

The repository already contains:

- A generated linkage map, crosswalk generator, normalisation/profile layer,
  deterministic and probabilistic matching, `MatchingConfig`, rejection memory,
  review views, a retrospective command, Celery hooks, and v2 migrations
  `0051`-`0053`.
- A version-controlled Python map at
  `va_explorer/va_data_management/linkage/linkage_map.py`.
- A generated CSV at
  `va_explorer/va_data_management/linkage/linkage_crosswalk.csv`, but the CSV is
  currently ignored by the repository-wide `*.csv` rule and is not tracked.
- Passing focused tests: 73 tests across the linkage and legacy matching test
  modules.

The local database has 3,252 Deaths and 2,681 VA-type records (2,665 VA and 16
VASA). All 2,681 have a persisted v1 `VADeathMatch`; no v2 reconciliation has
been applied. Persisted outcomes are 313 auto-linked, 95 pending review, 3
ambiguous, and 2,270 unmatched. There are no manual confirmations or rejection
rows in this database.

A fresh v2 dry-run produced:

| Metric | Result |
|---|---:|
| Auto-linked | 805 (30.0% of all VA/VASA) |
| Pending review | 926 |
| Ambiguous | 11 |
| Unmatched | 939 |
| Auto + queued candidate coverage | 65.0% |
| Community VA candidate coverage | 80.0% |
| Facility VA candidate coverage | 40.0% |
| VASA candidate coverage | 81.2% |
| Existing v1 links demoted for review | 2 |
| Same-model duplicate links reported | 8 |

The overall result does not satisfy the 75% prior. More importantly, the 65%
number is candidate coverage, not an achieved or validated link rate. It must
not be used as evidence that precision or recall is acceptable.

## Requirement Traceability

| Requirement area | Status | Audit result |
|---|---|---|
| Generated canonical crosswalk | Partial | Python artifact exists, but the reviewable CSV is untracked; the facility XLSForm is absent; VASA is hashed but not parsed/verified by this generator; group paths are not captured. |
| Human-confirmed mappings only | Partial | Proposed rows are inactive, but an absent seed marked `confirmed` can self-activate after a form change without a new sign-off. Missing WHO/model fields only warn in several cases. |
| Crosswalk-driven profiles | Partial | Core fields are mapped, but some active concepts are unused (`informant_relationship`, `death_registered`) and several accessors remain special-cased. |
| Deterministic rules and guards | Partial | Death ID, event, NRC, inert registration number, and exact composite exist. NRC currently auto-links without the required dedicated name decision path. The explicit `name_nrc_exact` / `name_nrc_probabilistic` rules, name thresholds, in-person name-conflict review, and duplicate-NRC review are not yet implemented. |
| Recall-oriented union blocking | Partial | Union blocks exist, but sex-disagreeing candidates are removed before scoring, single-record matching loses the geography-free DOD block, and arbitrary caps discard large blocks. |
| Probabilistic/Fellegi-Sunter-style scoring | Partial | The engine is an explainable weighted heuristic, not a calibrated probability or fitted FS model. Missingness can inflate scores because only present fields form the denominator. |
| 75% prior monitoring | Not met | Reports show the prior, but PASS is based on all queued rows as if they were likely true. There is no labelled-sample precision/recall estimate. |
| Preserve existing confirmed links | Not met | Normal runs skip manual links, but `--recheck/--rebuild --apply` can overwrite them. An evaluation error can also replace an existing auto-link with `ERROR`. |
| Auditable and reversible review | Partial | Confirm/reject and history exist, but there is no explicit unlink action; alternative confirmation retains stale evidence; rejection history is not what the Rejected tab displays. |
| Scheduled re-evaluation | Partial | New/updated ingestions are queued, but the sweeper only selects never-evaluated records. An unmatched VA is not reconsidered when a Death arrives later. |
| Command/reporting | Partial | The command is dry-run by default and has the required filters, but it loads the full Death index and all report rows, silently falls back for an unknown config, and has no resume checkpoint. |
| Tests | Partial | Focused synthetic tests pass, but cap recall, manual-link immutability under force, error preservation, location scoping, concurrency, unlinking, and real labelled precision/recall are not covered. |

## Critical Findings

### P0 - Link integrity and review safety

1. Manually confirmed links are not immutable to automation.
   `service.py` skips them only when `force_recheck=False`; the command maps
   `--recheck/--rebuild` directly to `force_recheck`, including in apply mode.
   A forced run can change the Death/status while leaving `is_manual=True` and
   stale reviewer metadata. This conflicts with the requirement that confirmed
   links are never clobbered.
2. Evaluation failures can destroy an existing auto-link. The exception path
   persists an `ERROR` result with no Death over the current row. Errors must be
   recorded without changing the last authoritative link.
3. Confirming a ranked alternative only changes the Death and method. The score,
   comparisons, block sources, and alternatives remain snapshots of the
   previously proposed Death, producing an incorrect audit record.
4. Rejection is not represented consistently. The action briefly writes status
   `REJECTED`, then immediately re-evaluates and overwrites that status. The
   Rejected tab queries `VADeathMatch.status=REJECTED` instead of
   `VADeathMatchRejection`, so it does not show durable rejection history.
5. There is no explicit unlink/revoke workflow despite the reversibility
   requirement. The generic admin remains editable and can bypass workflow audit
   semantics.
6. Confirm/reject actions do not lock the match row. Concurrent reviewers or a
   matcher task can overwrite one another. The `next` redirect is not restricted
   to local hosts.
7. Review querysets are not restricted with the existing user-location access
   helpers, even though Data Managers can be location-scoped elsewhere in VACMS.

### P0 - Candidate recall and false-negative risk

1. `union_block_candidates()` removes known sex disagreements before scoring.
   This loses data-entry-error matches and prevents contradiction evidence from
   reaching review.
2. Per-block results are sliced to 200 in insertion/PK order and the union is
   hard-capped at 300. The live dry-run had a median DOD block of 751 candidates
   (p95 835) and a facility province block of 1,322. The report does not expose
   how many candidates each per-block cap discarded. This is a direct and hidden
   recall loss.
3. A duplicate NRC does not create an NRC candidate block. It reaches review in
   the current synthetic test only because the fixture also shares date and
   geography; an isolated duplicate NRC can be missed entirely.
4. `DeathIndex.build_for_profile()` uses geography SQL filters and explicitly
   drops the geography-independent DOD block. Evaluation behavior therefore
   differs between single-record/review paths and batch/calibration paths.
5. Facility matching is not sufficiently resolved. Of 1,008 facility VAs, only
   534 currently derive a district; 474 point at the `Unknown` location while
   1,003 carry a hospital. The plan cannot defer a hospital/area-to-district
   crosswalk while facility candidate coverage is 40%.
6. Unresolved VA tool type is treated as facility, which skips the district term
   in exact composite matching and can broaden candidates incorrectly.
7. Community exact composite accepts two missing districts as equal. A community
   composite should require a non-empty district on both records.

### P1 - Score validity and calibration

1. The displayed 0-100 score is not a probability. It is agreement divided by
   the weights of fields present on both records. Two agreeing fields can produce
   100 even when most identifying evidence is absent; the current coverage guard
   permits name plus sex as the two fields.
2. Disagreements generally award zero rather than an evidence-based negative
   likelihood. This is not a fitted Fellegi-Sunter model.
3. Month-precision dates are represented as the first day of a month but can
   receive day-exact weight. Only year precision is discounted.
4. The seeded `auto=88` threshold has not been justified by a labelled precision
   study. The 75% population prior is a recall expectation and cannot justify
   lowering an auto-link precision threshold.
5. The calibration PASS condition counts every pending/ambiguous candidate as a
   likely true pair. Lowering the review threshold can therefore improve the
   reported PASS metric without improving linkage quality.
6. One global config is used for community VA, facility VA, and VASA despite
   materially different field availability and blocking behavior.
7. `config_version` stores a mutable config name, not an immutable revision or
   snapshot. The crosswalk version is not persisted on a match. An old result
   cannot be reproduced reliably after config/rule edits.

### P1 - Crosswalk correctness and provenance

1. `.gitignore` ignores `linkage_crosswalk.csv`; the required reviewable,
   version-controlled artifact was not included in commit `4cdfe46`.
2. The requirement names four authoritative XLSForms, but the facility form is
   only present as a data-bearing CSV. This is an unresolved source-provenance
   gap, not a completed verification.
3. `generate_linkage_crosswalk` hashes the VASA XLSForm but derives VASA names
   from the existing `field_map.py`; it does not independently parse and compare
   `WHO_name` values. The separate VASA generator helps, but neither command
   enforces both artifacts as one atomic drift check.
4. The shared survey parser does not emit group paths as required.
5. Crosswalk versioning hashes form files but not the rule table, generator
   version, choice-map rules, or facility header. Behavior can change while the
   stored crosswalk version remains unchanged.
6. Confirmed rows with missing WHO/model fields can remain active after only a
   warning. Regeneration must fail closed.
7. Category matching is order-sensitive. For example,
   `higher_than_secondary_school` is currently classified as `secondary` because
   the broad pattern is evaluated before the more specific `higher` pattern.
8. The generated proposal list demonstrates that label similarity alone is not
   safe (`DE_15` cause of death proposes `Id10019` sex at score 80). Proposed rows
   are correctly inactive, but approvals need reviewer/date/form-hash metadata.

### P2 - Operations, performance, and observability

1. Each Celery link batch builds a full Death index. Parallel batches duplicate
   the same CPU and memory work. The management command also retains every report
   row in memory even when no export is requested.
2. The hourly sweeper selects only `va_death_match__isnull=True`, not stale
   unmatched/error rows, so new Death records do not trigger re-evaluation.
3. Apply-mode runs discard per-block statistics, while dry-runs collect them.
4. Candidate metrics show pre-cap block sizes but not retained/dropped counts,
   candidate rank, zero-block rate, or candidate recall against known links.
5. Duplicate reporting groups all `VerbalAutopsy` rows together rather than
   distinguishing community and facility tools.
6. Dashboard review counts are global even when the Death timeline queryset is
   filtered by location/date.
7. `--config <missing-name>` silently falls back to defaults. `--dry-run` and
   `--apply` are not mutually exclusive, and `--va-id` does not implement the
   documented VASA `key` lookup.

## Revised Implementation Plan

Each phase below has an explicit exit gate. Do not start v2 apply/reconciliation
until Phases 0-4 pass.

### Phase 0 - Freeze and Baseline

- Keep `VA_DEATH_MATCH_ENABLED=False`; do not run v2 with `--apply`.
- Export the current v1 `VADeathMatch` rows and status/method counts, including
  history IDs, before any reconciliation.
- Save the current v2 dry-run JSON report as the comparison baseline without PII
  in version control.
- Agree with the data owner on:
  - the eligible denominator for the 75% prior, especially facility VAs;
  - the minimum acceptable auto-link precision (recommendation: at least 99%,
    with a reported confidence interval);
  - valid Zambia NRC formats/placeholders;
  - whether VASA DeathID and CMS Event are authoritative keys or strong evidence;
  - whether same-tool duplicate links can ever be legitimate.
- Obtain the authoritative facility XLSForm, or record a signed decision that
  community-WHO semantics plus the facility CSV header are the approved
  temporary surrogate.

Exit gate: baseline artifacts exist, production auto-linking remains disabled,
and the five data-owner decisions above are recorded.

### Phase 1 - Protect Authoritative Links and Decisions

- Make manual confirmation immutable to all matcher paths. `--recheck` may
  evaluate a manual link in shadow/report mode but must never persist over it.
  Replacing it requires an explicit reviewer unlink/reassign action.
- Preserve the last authoritative link on evaluation errors. Store error status
  and traceback metadata in a separate evaluation/audit record or error fields
  that do not clear the linked Death.
- Add explicit, POST-only confirm, reject, unlink, and reassign service methods.
  Require reason text for unlink/reassign and optionally for rejection according
  to the agreed policy.
- Add queryable `review_reason` and `review_mode` fields (or equivalent typed
  fields on the supporting evaluation model). Include `in_person` as a review
  mode and `nrc_match_given_name_conflict` as a reason; do not bury these only in
  free-text evidence JSON.
- Wrap each review action in `transaction.atomic()` and lock the match row with
  `select_for_update()`. Reject stale forms using the row version/evaluated time.
- When an alternative is selected, re-score it under the recorded config and
  crosswalk, then store that candidate's comparisons, score, and snapshot before
  confirmation.
- Make the Rejected tab query `VADeathMatchRejection`; keep the current proposal
  status independent of rejection history. Re-evaluate after rejection in
  review-only mode so the next candidate cannot auto-link without review.
- Add immutable decision events (confirm/reject/unlink/reassign, actor, reason,
  prior/new Death snapshots, config revision, crosswalk revision, timestamp).
  This may be a supporting model; `VADeathMatch` remains authoritative.
- Add database/application invariants for matched status -> non-null Death,
  manual status -> `is_manual=True` + reviewer, and no impossible VA-side state.
- Restrict review index/detail/actions to the reviewer's allowed VA/Death
  locations using existing location access utilities. Validate `next` with
  `url_has_allowed_host_and_scheme`.
- Make the generic admin read-only for match/rejection state, or route admin
  changes through the same decision services.

Exit gate: regression tests prove forced commands, exceptions, concurrent
actions, and admin access cannot overwrite a confirmed link; unlink/reassign is
audited and reversible.

### Phase 2 - Make the Crosswalk Reproducible and Fail Closed

- Add a narrow `.gitignore` exception and commit
  `linkage/linkage_crosswalk.csv`.
- Extend `survey_fields()` to capture group path, raw type, list name, English
  label, `WHO_name`, and repeat/instrument context.
- Parse E_DEATH, community WHO VA, VASA, and the authoritative facility form in
  the linkage generator. Compare VASA-derived mappings with
  `PSEUDO_TO_WHO`/`COMBINED_WHO`; fail `--check` on disagreement.
- If the facility XLSForm remains unavailable, mark that source explicitly
  `provisional` in the artifact and prevent silent claims that all four forms
  were verified.
- Replace implicit `confirmed` seeds with approvals bound to the current source
  hashes. New fields, changed labels/types/choice lists, and previously absent
  nickname fields must return to `proposed` until re-approved.
- Treat missing WHO IDs, missing model fields, conflicting duplicate WHO IDs,
  and unresolved choice lists as generator errors for active concepts.
- Fix choice parsing to use `select_list_name()` consistently, including the
  non-standard `select one` syntax.
- Make category rules most-specific-first and add collision diagnostics. Correct
  the education mapping and review assumptions such as mapping `other` place of
  death to community.
- Add an active-concept coverage report: `used_for_blocking`, `used_for_scoring`,
  `derived`, `unavailable`, or `unused`. Either wire active unused concepts into
  the vector or mark them inactive.
- Compute one immutable behavior revision from all form/header hashes, rule and
  override content, category rules, VASA map revision, and generator version.
  Persist this crosswalk revision on every evaluation/match.
- Add both generator `--check` commands to CI/pre-commit so artifact drift fails
  before merge.

Exit gate: a clean checkout regenerates byte-identical tracked artifacts; every
active row has current approval metadata and all source/mapping drift fails
closed.

### Phase 3 - Correct Candidate Generation and Scoring

NRC-gated name matching:

- Add `MatchMethod.NAME_NRC_EXACT = "name_nrc_exact"` and
  `MatchMethod.NAME_NRC_PROBABILISTIC = "name_nrc_probabilistic"`. Evaluate this
  path after format-valid DeathID and the guarded CMS Event bridge, but before
  the general probabilistic candidate path. Remove NRC-only automatic linking;
  NRC without sufficient name agreement must be reviewed, not asserted as a
  link.
- Build the NRC block from canonical profiles on both sides:
  `normalize_nrc(VA.Id10073) == normalize_nrc(Death.DE_08)`. The same canonical
  WHO field applies to VASA. Both values must be non-empty and pass the
  data-owner-approved NRC validity and placeholder rules.
- For each exact-NRC candidate, compare
  `VA.Id10017 + VA.Id10018` with `Death.DE_03` using normalized exact token-set
  equality plus a one-to-one name-component alignment. Persist at least:
  `overall_name_similarity`, `given_name_similarity`, `surname_similarity`,
  token pairs, unpaired tokens, and whether the result was exact or fuzzy.
- `name_nrc_exact`: auto-link without applying the general probabilistic score
  threshold when the normalized non-empty name token sets are equal and the NRC
  identifies one unique, non-rejected Death.
- `name_nrc_probabilistic`: auto-link a likely spelling variation only when all
  of the following are true:
  - the NRC identifies one unique, non-rejected Death;
  - overall name similarity is at or above `name_nrc_auto_similarity`;
  - surname similarity is at or above `name_nrc_surname_min_similarity`;
  - every required given-name component is at or above
    `name_nrc_given_min_similarity`;
  - there are no unexplained conflicting name tokens and no hard sex/DOD-year
    contradiction.
- Seed conservative shadow-mode values in `MatchingConfig` (provisional starting
  point: overall 95, surname 95, given name 90 on a 0-100 scale), then replace
  them with thresholds selected from labelled holdout precision. Do not activate
  fuzzy NRC-name auto-linking merely because these provisional values exist.
- Treat phonetic/Jaro-Winkler similarity as spelling evidence, not identity by
  itself. A close form such as a one-character transposition may qualify for
  auto-linking when every component clears its threshold. A matching surname
  must never compensate for a given name below the configured component
  threshold.
- Route every exact-NRC pair that is not safe to auto-link to
  `PENDING_REVIEW`, retaining the NRC evidence. Specifically, same surname plus
  a materially different first name must be tagged
  `review_reason="nrc_match_given_name_conflict"` and
  `review_mode="in_person"`. Low overall name similarity, a single-token name,
  ambiguous token alignment, duplicate Deaths sharing the NRC, and hard
  sex/DOD-year contradictions also require in-person review rather than an
  automatic link.
- Add `by_nrc` lookup support to the full and single-record Death indexes. If
  normalized fields are materialized in the database later, index normalized
  NRC and retain identical single-record and batch behavior.

Candidate generation:

- Remove sex as a candidate prune. Keep disagreement as scored contradiction
  evidence, and route deterministic-key conflicts to review.
- Add NRC and registration-number blocks even when values are non-unique, so all
  exact identifier collisions are ranked for review.
- Require non-empty equal districts for community exact composite; keep the
  facility exception only for records positively classified as facility.
- Introduce an explicit `unknown` tool classification. Unknown records must not
  inherit the looser facility rule.
- Make single-record and batch evaluation use the same logical blocks. Implement
  bounded DOD/name/identifier lookups rather than dropping geography-free blocks.
- Build a reviewed facility/area/hospital -> canonical Location/district map and
  instrument unmapped/ambiguous facilities. Use `resolve_location_context()` and
  preload location ancestry to avoid N+1 queries.
- Replace first-PK truncation with deterministic cheap pre-ranking inside broad
  blocks (identifier, name-token/phonetic similarity, DOD proximity, geography),
  then apply configurable caps. Record pre-cap, retained, and dropped counts per
  block. A known true pair must not be dropped by a cap.
- Make phonetic blocking robust to name order by indexing suitable name tokens,
  not only the last token of a free-text Death name.

Scoring:

- Treat missing evidence as neutral without renormalising a sparse pair into a
  high confidence. Until a fitted model exists, use a fixed score scale plus a
  minimum-evidence policy requiring a strong temporal/identifier/geographic
  signal in addition to name; name + sex alone must not auto-link.
- Give contradictions explicit, configurable negative evidence while retaining
  hard guards for deterministic sex/DOD-year conflicts.
- Handle day, month, and year date precision separately. Month/month agreement
  must not receive day-exact weight.
- Validate NRC using the data-owner-approved format and reject known placeholder
  patterns. NRC equality selects the strong candidate block; exact or
  threshold-qualified component-safe name agreement controls auto-linking. All
  other NRC matches go to in-person review.
- Revisit greedy name alignment so one token cannot satisfy multiple tokens;
  retain token-order permutations and `Baby of` behavior.
- Add or remove the active but unused relationship/death-registration concepts.
- Split weights/thresholds by tool, or include tool-specific missingness and
  calibration in a single explicit model.

Exit gate: candidate recall is 100% for preserved manual links and the labelled
gold set before and after caps; synthetic tests cover missing/contradictory data,
all tools, broad blocks, name order, partial dates, and duplicate identifiers.

### Phase 4 - Build Defensible Calibration

- Create a versioned, de-identified gold set of candidate pairs sampled by tool,
  site, score band, deterministic method, duplicates, and unmatched cases.
  Require two reviewers and adjudication for disagreements.
- Split calibration and holdout sets. Do not tune and report final metrics on the
  same labels.
- Report, by tool and overall:
  - candidate-pair recall (true Death appears anywhere in the candidate set);
  - post-cap candidate recall;
  - auto-link precision and recall with confidence intervals;
  - review-band precision/yield and reviewer disagreement;
  - authoritative achieved link rate (`matched_auto + matched_manual`);
  - candidate coverage as a separate diagnostic, never a PASS substitute;
  - queue backlog/age, no-candidate rate, truncation rate, and duplicate rate.
- Report `name_nrc_exact` and `name_nrc_probabilistic` separately, including the
  fuzzy rule's auto-link precision, similarity distribution, in-person-review
  yield, and false-positive examples. The fuzzy NRC-name rule must meet the
  approved auto-link precision gate independently of the overall engine.
- Fit Fellegi-Sunter m/u weights with smoothing when the labelled sample is large
  enough, or explicitly retain a heuristic score and stop calling it probability
  or confidence. Calibrate any displayed probability on the holdout set.
- Validate `MatchingConfig` JSON against a schema: allowed keys, numeric ranges,
  non-negative base weights, ordered thresholds, candidate limits, and per-tool
  sections. Include and validate `name_nrc_auto_similarity`,
  `name_nrc_surname_min_similarity`, and `name_nrc_given_min_similarity`.
  Unknown config names must fail the command.
- Make configs immutable revisions. Activation creates/selects a revision; a
  match stores the revision ID/hash and full effective config snapshot needed for
  reproduction.
- Change the 75% report to show two lines:
  - achieved authoritative link rate versus the prior;
  - candidate coverage versus the prior as an investigation diagnostic.
  Queued rows count as links only after reviewer confirmation.
- Do not activate the seeded `auto=88` value unless the holdout precision gate is
  met. Use the approved precision target to select the auto threshold and use the
  review threshold to pursue recall.

Exit gate: holdout metrics meet the agreed precision gate, candidate recall is
documented, and the 75% comparison cannot be gamed by widening the review queue.

### Phase 5 - Complete the Review Workflow

- Show current candidate and each ranked alternative with its own field-by-field
  evidence and immutable evaluation revision.
- Add filters for tool, location, status, method, score band, contradiction,
  review reason/mode, evaluation version, and age in queue. Preserve filters
  safely through actions.
- Show durable rejection and decision history, including unlinks/reassignments.
- Calculate duplicate groups by actual tool (`community_va`, `facility_va`,
  `vasa`), not only by Django model. Let reviewers resolve a duplicate group
  without losing either record's history.
- Add explicit `no credible candidate` / `no death expected` decisions if the
  data owner approves their semantics; do not infer them solely from low score.
- Add queue ownership/SLA metrics if review volume remains near the current 937
  records.

Exit gate: a location-scoped reviewer can safely resolve every queue state,
including reject-next-candidate, duplicate, unlink, and reassign, with complete
evidence and audit history.

### Phase 6 - Scheduling, Performance, and Monitoring

- Replace the binary feature flag with explicit modes such as `off`, `shadow`,
  `review_only`, and `auto`. Shadow mode evaluates and reports without asserting
  links.
- Re-evaluate unmatched/error/stale records when relevant VA data, Death data,
  crosswalk revision, or active config revision changes. Use timestamps/revision
  markers and rejection exclusions to avoid churn.
- Avoid rebuilding the full Death index in every parallel Celery batch. Build it
  once per orchestrated run, use a revisioned cache, or use database-backed
  indexes/materialised normalized fields after profiling both approaches.
- Stream CSV/JSONL report rows instead of retaining all rows in memory. Add
  checkpoint/resume by stable VA PK and config/crosswalk revision.
- Collect the same block metrics in dry-run and apply mode. Surface errors and
  truncation as task failures/warnings rather than returning only total/changed.
- Scope dashboard queue metrics to the active dashboard filters and add per-tool
  achieved link rate, review backlog, truncation, and stale-evaluation metrics.
- Validate command option combinations and positive limits/batch sizes. Support
  both `instanceid` and VASA `key` for `--va-id`.
- Benchmark query count, wall time, and peak memory on at least 10x current data;
  define task timeouts and safe batch/concurrency settings from the result.

Exit gate: shadow runs are resumable, bounded in memory, behaviorally identical
to single-record evaluation, and observable by tool/block/revision.

### Phase 7 - Reconcile and Roll Out

1. Deploy Phases 1-6 with mode `off`; run migrations and both crosswalk drift
   checks.
2. Run a full shadow calibration and compare every existing v1 authoritative
   link. Every missing/changed v1 link must have a machine-readable reason and a
   review disposition. Manual links must be byte-for-byte unchanged.
3. Resolve the two currently demoted v1 links and eight duplicate groups before
   broad apply.
4. Canary in `review_only` by tool. Start with community VA and VASA only after
   their holdout gates pass. Keep facility VA review-only until facility mapping
   and its separate precision/recall gates pass.
5. Apply reconciliation in resumable batches. Persist run ID, config revision,
   crosswalk revision, before/after counts, failures, and reviewer queue delta.
6. Enable `auto` only for a tool whose holdout precision gate passed. Monitor
   daily initially, then weekly after stable review yield and link rates.
7. Roll back by switching mode/config revision and reversing explicit decision
   events where required. Do not delete match/history tables as an operational
   rollback mechanism.

Exit gate: authoritative link rate and precision meet the approved targets for
each enabled tool, no confirmed link was overwritten, and rollback has been
rehearsed on a production-like snapshot.

## Required Test Additions

- Crosswalk: tracked CSV, all source hashes, VASA-map mismatch, group paths,
  fail-closed missing fields, approval invalidation on form drift, category-rule
  collision/specificity, and both generator checks in CI.
- NRC-gated name matching: VA and VASA exact success cases, swapped name order,
  punctuation/case normalization, high-similarity misspellings above/below each
  configured threshold, invalid/placeholder NRC, blank/single-token names,
  rejected-pair exclusion, duplicate NRC ambiguity, and sex/DOD-year
  contradiction review. Include a mandatory same-surname/different-first-name
  fixture that asserts `PENDING_REVIEW`, `review_mode="in_person"`, and no
  authoritative link even when aggregate name similarity exceeds the review
  threshold.
- Candidate generation: sex contradiction retained, isolated duplicate NRC,
  single-vs-batch parity, broad-block cap retention, cap metrics, community
  missing-district composite, unknown tool, facility hospital mapping, and
  name-order phonetics.
- Scoring: sparse name+sex cannot auto-link, explicit contradiction penalties,
  day/month/year precision, one-to-one token alignment, identifier validity, and
  per-tool config behavior.
- Persistence: manual link unchanged under every command flag, evaluation error
  preserves existing link, config/crosswalk snapshots, no history churn on truly
  identical evaluation, and database state constraints.
- Review: alternative evidence updated, rejection history tab, next candidate is
  review-only, unlink/reassign audit, stale/concurrent POST protection, local-only
  redirect, location scoping, and read-only admin.
- Scheduling/command: stale unmatched re-evaluation after new Death, no duplicate
  task churn, apply-mode block metrics, missing config failure, mutually exclusive
  modes, VASA key lookup, streaming report, and resume checkpoint.
- Calibration: fixed labelled fixtures, holdout split, confidence intervals,
  achieved-vs-candidate coverage separation, per-tool metrics, and a regression
  test proving a lower review threshold cannot by itself turn the authoritative
  75% verdict into PASS.

## Verification Commands

```bash
/srv/vacms/shared/venv/bin/python manage.py generate_vasa_mapping --check
/srv/vacms/shared/venv/bin/python manage.py generate_linkage_crosswalk --check
/srv/vacms/shared/venv/bin/pytest -q \
  va_explorer/va_data_management/tests/test_linkage_*.py \
  va_explorer/va_data_management/tests/test_va_death_matching.py
/srv/vacms/shared/venv/bin/python manage.py link_deaths_to_vas \
  --calibrate --config <immutable-revision> --export-report /tmp/linkage-calibration.csv
pre-commit run --all-files
```

Run the broader VA/VASA ingestion, location-scoping, task, analytics, and user
permission suites before rollout. Compare full-suite failures against the
pre-change baseline rather than accepting newly introduced failures as known.
