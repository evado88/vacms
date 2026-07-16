Claude Code Prompt: Link E_DEATH Records to VA/VASA Records in VACMS (v2 — improves existing implementation)
Objective
VACMS already has a death↔VA linkage implementation. Audit it first, then design and present an implementation plan (implement on approval) that improves it for linking death notification records (ODK form `E_DEATH`, v1.2.4) to verbal autopsy records from three tools, irrespective of type:
Community VA — `znphi_va_who_v1_5_2_7.xlsx` (WHO 2016 VA; fields named directly `Idxxxxx`; geo: province/district/constituency/ward/ea)
Facility VA — `zm_va_who_v1_5_2_7.xlsx` (same `Idxxxxx` fields; geo: province/area/hospital only)
VASA — `znphifacilityvasa_02_final.xlsx` (form id `znphi_vasa_who_2016`; fields have pseudo-names in `name` — `Q1xxx`/`N2xxx`/`C3xxx`/`A4xxx` — with the WHO Id in the `WHO_name` column)
Recall target: at least ~75% of existing VA/VASA records are known to have a corresponding death record. The improved algorithm must be tuned to produce as many true/likely linkages as possible — measure the achieved link rate (auto + review-confirmed) against this prior; a materially lower rate means the algorithm (blocking, thresholds, or crosswalk) is losing true matches and must be revisited. Prefer routing borderline pairs to human review over leaving them unlinked.
Constraints: each VA/VASA record links to at most one death; a death may have several VA-type records. Links must be auditable, reversible, human-reviewable. Existing confirmed links from the current implementation must be preserved/migrated, never clobbered.
Layer 1 — Canonical variable crosswalk (generated from the ODK definition forms)
The linkage feature space must be defined once, at WHO-Id level, and resolved per tool through a generated crosswalk. The four XLSForm definition files are the primary reference documents — do not hardcode mappings that can be derived from them:
Parse the `survey` sheets of all four forms. For each field capture: `name`, `WHO_name` (VASA only), `type`, English label (`label::English…` — column header spelling varies per form), and group path. Skip `note`/`begin/end group|repeat` rows.
VASA → WHO Id resolution: a VASA field participates in linkage under its `WHO_name` value (e.g. `Q1209` has `WHO_name` `Id10061`, label "Name of father or household head?" → it is the same variable as `Id10061` in the community/facility VA forms). Handle the known non-standard `WHO_name` variants (`Id10017/18` combined name, `_units`/`z`/`_check` helpers) with explicit rules; instrument-section duplicates of one WHO Id (`N2023`/`C3012`/`A4001` → `Id10017`) coalesce per record since sections are mutually exclusive.
E_DEATH ↔ WHO Id crosswalk via label analysis: E_DEATH fields (`DE-xx`) have no WHO_name column, so build the crosswalk by analysing the English label texts of E_DEATH against the WHO-form labels (normalize labels: strip the `(Idxxxxx)`/`[DE-xx]` prefixes, bracketed interviewer notes, and `${…}` references; lowercase; then token-overlap / fuzzy similarity plus concept keywords). Emit the proposed crosswalk as a reviewable, version-controlled artifact (CSV/JSON: `de_field, who_id, vasa_names, label_de, label_who, confidence, rule`) and get it human-confirmed before it drives linking. Regeneration must be a management command so form revisions update the crosswalk.
Seed/expected crosswalk (verify each against the actual labels; extend with whatever else the label analysis surfaces):
Concept	E_DEATH	WHO Id (community & facility VA)	VASA field(s) carrying that WHO_name
Deceased name	`DE-03` (full), `DE-03A` nickname	`Id10017` first + `Id10018` surname	`Q1202` (`Id10017/18`); `N2023`/`C3012`/`A4001` (`Id10017`) + surname equivalents
Head of household / father	`DE-02` (+`DE-02A`)	`Id10061` "name of the father"	`Q1209` (WHO_name `Id10061`)
Mother's name	— (`DE-03` may read "Baby of <mother>")	`Id10062`	`Q1208`
Sex	`DE-05`	`Id10019`	`Q1601` (+`Q1203` screening)
Date of birth	`DE-04`	`Id10021` (+`Id10020` known?)	`Q1603` (+`Q1204` screening text)
Date of death	`DE-06`	`Id10023` (calc; `Id10023_a/b`), `Id10024` year	`Q1606`, `Id10024` (+`Q1205` screening text)
Age	`age` calc	`ageInDays`/`ageInYears`/`age_group`, `isNeonatal/isChild/isAdult`	VASA age calculates + populated section (N2/C3/A4)
Place of death	`DE-07`	`Id10058` (+`Id10057` village)	`Q1604`
Usual residence	`DE-22` (informant addr — verify)	`Id10055`	per crosswalk
NRC / national ID	`DE-08`	`Id10073`	field with WHO_name `Id10073`
Nationality	`DE-09`	`Id10052`	per crosswalk
Marital status	`DE-11`	`Id10059`	per crosswalk
Occupation	`DE-12`	`Id10066`	per crosswalk
Education	`DE-13`	`Id10063`	per crosswalk
Death registration	`DE-30` (registered y/n)	`Id10069_a`, `Id10070` (reg number), `Id10071` (date), `Id10462` (certificate)	`N2284`/`C3364`/`A4364` (`Id10070`)
Died pregnant	`DE-16`	`Id10305` (+`Id10310` negation)	per crosswalk
Died during childbirth	`DE-17`	`Id10312`	per crosswalk
Died ≤42 days post-pregnancy	`DE-18`	`Id10306`/`Id10315` (6 weeks)	per crosswalk
Informant/respondent name	`DE-19`	`Id10007`	per crosswalk
Relationship to deceased	`DE-25`	`Id10008`	per crosswalk
Explicit death ID	VACMS-assigned Death ID (form has no field; `substr(${DeathID},5,6)` in VASA implies a structured format — find the generator)	—	`DeathID` (+`VAID_decide`)
Geography	province, district, constituency, ward, ea	community: same five; facility: province, area, hospital	province, district, constituency, ward, ea
GPS	`DE-34-*`	`geopoint-*`	`GPS-*`
ODK identity	`instanceID`/`KEY`	same	same
The four export CSVs (`E_DEATH.csv`, `znphi_va_who_v1_5_2_7.csv`, `zm_va_who_v1_5_2_7.csv`, `znphi_vasa_who_2016.csv`) are header-only column schemas — use them to verify flattened column names; the XLSForms remain authoritative for semantics.
Data-quality realities to tolerate: free-text names with spelling/ordering variants and nicknames; VASA screening dates are text; facility VA lacks district/ward/EA (hospital only — map via facility register); DOB often unknown; neonate names as "Baby of <mother>"; `DeathID` present only when the interviewer had it; select-one answer codes differ per form (build choice-mapping tables from the `choices` sheets, e.g. `DE-05` vs `Id10019` sex codes, `DE-07` vs `Id10058` place-of-death categories).
Layer 2 — The linkage algorithm
Stage 0 — Normalization (shared utility, crosswalk-driven). For every record build a normalized linkage profile keyed by WHO Id / canonical concept: names → lowercase, trim, strip punctuation/honorifics, token sets (compare E_DEATH full name against `Id10017+Id10018` concat and token permutations; include `DE-03A`/`DE-02A` nicknames; neonates: match `DE-03` "Baby of X" against `Id10062` and compare `DE-02`↔`Id10061` household-head names); sex → canonical M/F via choice maps; dates → ISO with defensive parsing of text dates, preserving partial (year/month) precision; age → days at death; geography → canonical location FKs (facility `hospital` → district lookup); GPS → decimal degrees; IDs (NRC, death reg no., DeathID) → uppercase alphanumeric.
Stage 1 — Deterministic linking (auto-link, precedence order, each with a contradiction guard — sex or DOD-year conflict ⇒ review instead of link):
`VASA.DeathID` == VACMS Death ID (format-validated) → method `death_id`.
`Id10073` == `DE-08` (valid, non-empty NRC) → method `nrc`.
`Id10070` == death registration number held for the death record (verify availability in VACMS) → method `death_reg_no`.
Exact composite: name token-set equality AND sex AND exact DOD AND same district (geo term skipped for facility VA) → method `exact_composite`.
Stage 2 — Probabilistic linking (Fellegi–Sunter-style score; recall-oriented). For unlinked VA records:
Blocking (union of blocks — generous, since missing a block loses a true match): (a) district + year of death; (b) constituency + ward; (c) DOD ± 60 days regardless of geography; (d) name-initials + death year; (e) soundex/metaphone of surname + district; facility VA: province + facility-derived district. Report per-block candidate counts.
Comparison vector, weights configurable: name similarity (Jaro-Winkler over best token alignment; nickname and household-head (`DE-02`↔`Id10061`/`Q1209`) hits as supporting evidence); sex agree/disagree (disagree strongly negative); DOD distance bands (exact / ≤7d / ≤30d / ≤90d; year-only at reduced weight); DOB/age-in-days relative difference; age-group agreement; place-of-death category agreement via choice maps; mother's-name similarity (neonate/child); marital status / occupation / education / nationality agreement (low weights — supporting only); pregnancy-flag consistency (`DE-16/17/18` vs `Id10305/Id10312/Id10306`) for female adult deaths; GPS proximity bands; ward/EA agreement. Missing values contribute zero, never negative.
Decision: score ≥ upper threshold → auto-link (`probabilistic`, store score + per-field contributions); middle band → review queue with ranked candidates; below → unlinked. Ties within a margin → review. Enforce one death per VA; flag same-tool duplicates on one death.
Calibration against the 75% prior: run in report-only mode on production data; tune blocking and thresholds until (auto + review-queued likely) candidate coverage is consistent with ≥75% of VAs having a real death record; publish precision/recall estimates from a manually labelled sample; keep weights/thresholds in a config table with an audit trail. Compare coverage against the existing implementation's links (any link the old system made that the new one misses must be explained or fixed).
Stage 3 — Review, migration & audit. `RecordLink` model (FKs to death + VA/VASA, tool type, method, score, matched-field snapshot JSON, status `auto|confirmed|rejected|pending_review`, created/reviewed by/at). Migrate existing implementation's links (confirmed ones as `confirmed`; recompute the rest and reconcile). Review UI per existing VACMS conventions; rejecting a pair excludes it from future runs; all link/unlink events audited.
Implementation requirements
Explore first: the current linkage implementation (models, matching logic, its link rate and failure modes — summarize), death record model/ingestion, `VerbalAutopsy` + VASA models, location models, Death ID generator. Present findings + plan before coding.
Deliverables: crosswalk generator command (XLSForm label analysis → reviewable artifact); normalization utilities; linkage engine (stages 1–2) with config table; `RecordLink` + migrations (incl. migration of legacy links); command `link_deaths_to_vas` (`--dry-run`, `--tool`, `--since`, `--rebuild`) with summary report (per-method links, queue size, unlinked, contradictions, link-rate vs 75% prior); review workflow; scheduled-task hook.
Tests: crosswalk generation (incl. `Q1209`→`Id10061`→`DE-02` and the `Id10017/18` combined case); normalization ("Baby of", nicknames, text dates, partial dates, choice-code maps); each deterministic rule + contradiction guard; blocking recall (every synthetic true pair shares ≥1 block); scoring fixtures (clear match / near-miss / tie); facility-VA path; idempotency; rejected-pair exclusion; legacy-link migration.
Follow existing VACMS style and audit conventions; document the crosswalk pipeline, recalibration procedure, and how the 75% prior is monitored.
Verify every field/column name above against the XLSForms and CSV schemas before use; where this prompt and the files disagree, trust the files and flag the discrepancy.
