You are working on the Verbal Autopsy and Case Management System (VACMS), a Django-based application.

Your task is to design and implement a safe, auditable, reusable, and fully integrated process for linking each Verbal Autopsy record to its corresponding Death record using the Death record’s unique key or primary identifier.

The implementation must include all necessary application scaffolding, database changes, reusable services, management commands, ingestion hooks, tests, logging, documentation, and operational safeguards.

## Objective

Establish a reliable relationship between Verbal Autopsy records and Death records while avoiding incorrect or forced matches.

The linking process must operate in two contexts:

1. A retrospective process that evaluates Verbal Autopsies already stored in VACMS.
2. An automatic process that evaluates and, where sufficiently confident, links each Verbal Autopsy whenever it is newly loaded or updated in VACMS.

The same central matching logic must be used in both contexts so that matching behaviour remains consistent regardless of how the Verbal Autopsy enters the system.

## Required matching priority

The matching process must follow the priority order below.

### Priority 1: Match through the Case Management event

Where a Verbal Autopsy is associated with a Case Management event, identify the Death record linked to that Case Management event.

Confirm that the Verbal Autopsy, Case Management event, and Death record refer to the same deceased person by comparing the common information available across the records.

Relevant comparison fields may include:

* Deceased first name
* Middle name
* Surname
* Full name
* Sex
* Age
* Date of birth
* Date of death
* Place of death
* Province
* District
* Constituency
* Ward
* Enumeration area
* Facility
* Household identifier
* Death record identifier
* Verbal Autopsy identifier
* Case Management event identifier
* Interviewer or enumerator
* Supervisor
* Respondent details
* Any other reliable demographic, geographic, event, household, facility, or administrative fields found in the VACMS models

A Case Management relationship must not automatically be treated as a confirmed match if the demographic or event details materially conflict.

The presence of a Case Management event should increase confidence and narrow candidate selection, but it must not override strong contradictory evidence.

### Priority 2: Fuzzy demographic matching

Where no usable Case Management event exists, compare the Verbal Autopsy directly with available Death records.

Use fuzzy and probabilistic matching based on the strongest available demographic, geographic, temporal, household, facility, and administrative fields.

The matching process should account for:

* Spelling variations
* Typographical errors
* Different name ordering
* Missing middle names
* Initials
* Extra whitespace
* Differences in capitalisation
* Approximate ages
* Partially recorded dates
* Minor differences between date of birth and reported age
* Minor date-of-death discrepancies where justified
* Alternative facility or location naming
* Missing values
* Differences caused by data-entry or ODK ingestion formats

Do not treat missing values as positive evidence of a match.

## Important data limitation

Some Verbal Autopsies were completed without a corresponding Death record being created in VACMS. This is particularly likely for facility-based Verbal Autopsies.

Therefore:

* Do not assume that every Verbal Autopsy must have a matching Death record.
* Do not force a match.
* Leave the Verbal Autopsy unlinked where there is insufficient evidence.
* A match must only be automatically accepted where the calculated match confidence is strictly greater than 90%.
* A score of exactly 90% or below must not be automatically linked.
* Cases scoring from 80% to 90%, inclusive, must be flagged for manual review.
* Cases below 80% must remain unlinked unless manually reviewed and confirmed.
* Where two or more Death records have similar scores, do not select one unless the leading candidate is clearly distinguishable from the alternatives.
* A Verbal Autopsy with no credible Death record candidate must remain unmatched.
* The absence of a match must be treated as a valid outcome rather than a processing failure.

## Mandatory implementation architecture

The implementation must not consist only of an isolated script or management command.

Create a reusable application-level matching service that can be called consistently from:

* PyODK Verbal Autopsy ingestion
* Django management commands that load or import Verbal Autopsies
* Any ETL or CSV import process that creates or updates Verbal Autopsies
* Administrative or user-driven Verbal Autopsy creation
* Future VACMS ingestion mechanisms
* A retrospective bulk-linking management command
* Any approved manual re-evaluation or reprocessing workflow

The central matching logic must exist in one reusable service or domain module and must not be duplicated across ingestion pathways.

A suggested structure may include modules such as:

* `services/va_death_linking.py`
* `services/matching/normalization.py`
* `services/matching/scoring.py`
* `services/matching/candidate_selection.py`
* `services/matching/results.py`
* `management/commands/link_verbal_autopsies.py`

Adapt the structure to the existing VACMS project conventions after inspecting the repository.

Do not invent application or module names without first checking the codebase.

## Required reusable auto-link function

Implement a reusable function or service method that evaluates a single Verbal Autopsy and links it to the most appropriate Death record when the required confidence threshold is met.

The function should conceptually support behaviour similar to:

```python
result = auto_link_verbal_autopsy_to_death(
    verbal_autopsy=va,
    save=True,
    force_recheck=False,
    source="pyodk_ingestion",
)
```

The exact function name, signature, return type, and location should follow the existing VACMS architecture.

The auto-link function must:

1. Accept a specific Verbal Autopsy instance or identifier.
2. Check whether the Verbal Autopsy already has a manually confirmed Death record link.
3. Avoid overwriting manually confirmed links.
4. Identify candidate Death records.
5. Prioritise Case Management-based matching.
6. Fall back to fuzzy demographic matching where necessary.
7. Calculate an explainable confidence score.
8. Identify contradictory values.
9. Determine whether the result is:

   * Automatically matched
   * Pending manual review
   * Ambiguous
   * Unmatched
   * Skipped
   * Already linked
10. Persist the relationship only where the confidence is strictly above 90% and all automatic-linking safeguards are satisfied.
11. Store or generate a detailed explanation of the match.
12. Return a structured result object or dictionary containing:

* Verbal Autopsy identifier
* Selected Death record identifier
* Matching method
* Confidence score
* Match status
* Supporting fields
* Contradictory fields
* Missing fields
* Alternative candidates
* Whether a database change was made
* Processing source
* Algorithm version

13. Be safe to run repeatedly.
14. Be transaction-safe.
15. Be usable independently from HTTP requests, management commands, and ingestion scripts.

## Automatic linking during ingestion

Integrate the auto-link function into every identified pathway through which Verbal Autopsies are loaded into VACMS.

This must include, at minimum:

### PyODK ingestion

After a Verbal Autopsy has been successfully validated and saved through the PyODK ingestion process:

1. Call the shared auto-link function.
2. Ensure the Verbal Autopsy transaction has completed sufficiently for related data to be queried.
3. Avoid calling the matcher on incomplete or invalid records.
4. Log the ingestion source as PyODK.
5. Do not fail the entire ingestion operation solely because no Death record match is found.
6. Do not silently ignore unexpected matching errors.
7. Capture errors for investigation without leaving the Verbal Autopsy in an inconsistent state.
8. Avoid re-linking records unnecessarily on every synchronisation where no relevant fields have changed.

### Management-command ingestion

For every management command that loads, imports, synchronises, or updates Verbal Autopsies:

1. Identify where the Verbal Autopsy record is committed to the database.
2. Call the same shared auto-link function after a successful save.
3. Record the specific command or import source.
4. Support disabling auto-linking through an explicit option where operationally necessary.
5. Support deferring matching until the end of the import where this is more efficient and does not compromise correctness.
6. Avoid performing duplicate matching where the same record is processed repeatedly in one import run.

### Other ingestion pathways

Inspect the codebase for all other processes that create or materially update Verbal Autopsies, including:

* Django forms
* Admin actions
* API endpoints
* Scheduled jobs
* Celery or background tasks
* ETL processes
* CSV or JSON loaders
* Data migration scripts
* Manual import utilities
* Existing synchronisation services

Integrate the shared auto-link function into each relevant pathway.

Where direct integration into every pathway would create excessive coupling, implement an appropriate application-level orchestration pattern that still ensures consistent matching.

Do not rely only on Django signals unless the codebase and transaction behaviour make signals the safest option.

Before using signals, assess:

* Transaction boundaries
* Bulk creation behaviour
* Recursive saves
* Performance
* Error handling
* Testability
* Whether all ingestion methods trigger the signal
* Whether related records are available when the signal executes

Prefer explicit service invocation where practical and auditable.

## Retrospective Django management command

Create a Django management command that can evaluate and link existing Verbal Autopsy records.

The command should follow the project’s naming conventions and support options comparable to:

```bash
python manage.py link_verbal_autopsies --dry-run
python manage.py link_verbal_autopsies --apply
python manage.py link_verbal_autopsies --va-id <identifier>
python manage.py link_verbal_autopsies --district <district>
python manage.py link_verbal_autopsies --facility <facility>
python manage.py link_verbal_autopsies --from-date <date>
python manage.py link_verbal_autopsies --to-date <date>
python manage.py link_verbal_autopsies --status unmatched
python manage.py link_verbal_autopsies --limit 100
python manage.py link_verbal_autopsies --recheck
python manage.py link_verbal_autopsies --export-report <path>
```

The exact options should be adapted to the project.

The management command must:

* Use dry-run mode by default.
* Require an explicit option before saving links.
* Process records in manageable batches.
* Avoid loading the full dataset into memory.
* Use the same reusable matching service used by ingestion.
* Skip manually confirmed links unless specifically authorised for review.
* Avoid overwriting existing valid links.
* Support filtering and limited test runs.
* Produce clear console output.
* Produce a CSV or structured report.
* Report counts for:

  * Automatically matched
  * Pending manual review
  * Ambiguous
  * Unmatched
  * Already linked
  * Skipped
  * Failed
* Exit with an appropriate status code where processing errors occur.
* Be idempotent.
* Support safe resumption after interruption where practical.

## Before changing code

Before implementing anything:

1. Inspect the relevant Django applications, models, managers, services, signals, serializers, forms, migrations, ingestion processes, and management commands.
2. Identify the exact models and fields representing:

   * Verbal Autopsy
   * Death record
   * Case Management event
   * Interviewer or enumerator
   * Household
   * Facility
   * Geographic location
3. Identify how PyODK data is currently pulled, transformed, validated, and saved.
4. Identify every management command or loader that creates or updates Verbal Autopsies.
5. Identify all additional ingestion pathways.
6. Determine whether an existing foreign key, one-to-one field, unique key, event identifier, or linking field already exists.
7. Identify existing data-quality rules, duplicate detection logic, management commands, scheduled jobs, ETL processes, or matching utilities that can be reused.
8. Identify existing transaction boundaries and bulk-ingestion patterns.
9. Identify whether the project already uses service modules, selectors, model managers, signals, tasks, or domain-layer abstractions.
10. Ask questions before proceeding where the model relationships, identifiers, expected workflow, or business rules are unclear.

Do not invent field names, relationships, application names, or ingestion behaviour without confirming them from the codebase.

## Matching rules

Develop an explainable weighted scoring model.

The scoring model should assign higher importance to stronger identifiers and lower importance to weaker or less reliable fields.

Suggested weighting principles include:

* Exact unique identifier match: very high weight
* Valid Case Management-to-Death relationship: very high weight
* Exact date of death: high weight
* Exact or strongly similar deceased name: high weight
* Date of birth: high weight where complete
* Household identifier: high weight
* Sex: moderate weight
* Age: moderate weight, with an appropriate tolerance
* Facility: moderate weight
* Geographic location: moderate weight
* Interviewer: supporting evidence only
* Supervisor: supporting evidence only
* Respondent information: supporting evidence only
* Submission date proximity: weak supporting evidence only

Interviewer, supervisor, facility, or location information must not be sufficient on its own to establish a match.

The scoring logic must distinguish between:

* Exact matches
* Normalised matches
* Fuzzy matches
* Compatible values
* Contradictory values
* Missing values
* Unavailable fields

Strong contradictions should reduce the score.

Examples include:

* Different sex
* Materially different dates of death
* Clearly incompatible ages or dates of birth
* Different deceased names with low similarity
* Different household identifiers where both are present
* Different districts, facilities, or locations where both are reliably recorded
* A Case Management event that clearly refers to another deceased person

Consider adding hard disqualification rules for combinations of serious contradictions.

## Confidence classifications

Use the following minimum classifications:

* Greater than 90%: eligible for automatic linking
* 80% to 90%, inclusive: manual review required
* Below 80%: do not link
* No credible candidate: unmatched
* Multiple similarly ranked candidates: ambiguous and manual review required

The final thresholds should be configurable rather than permanently hard-coded where practical.

Also define a configurable minimum score difference between the highest-ranked and second-highest-ranked candidate before an automatic match is permitted.

For example, a candidate scoring 94% should not automatically link where another candidate scores 93% unless an additional deterministic rule safely distinguishes them.

## Name and data normalisation

Create reusable normalisation functions for fields such as:

* Names
* Identifiers
* Dates
* Sex
* Ages
* Facilities
* Locations
* Household references
* Interviewer identifiers

Name normalisation should consider:

* Lowercasing
* Unicode normalisation
* Removing unnecessary punctuation
* Removing duplicate spaces
* Normalising common prefixes or titles
* Handling missing middle names
* Comparing individual name components
* Comparing full names after normalisation
* Supporting reversed name order where appropriate
* Avoiding over-normalisation that could create false matches

Do not remove meaningful name components in a way that creates unsafe matches.

## Candidate selection

Avoid comparing every Verbal Autopsy with every Death record where the dataset is large.

Use blocking or candidate filtering before fuzzy scoring.

Candidate filtering may use:

* Case Management relationship
* Date-of-death range
* District
* Facility
* Sex
* Age range
* Household identifier
* Geographic location
* Interviewer
* Submission period
* Known event identifiers

The blocking process must remain broad enough to avoid excluding legitimate matches.

Where initial blocking produces no candidates, allow a carefully controlled broader search.

## Database design

Inspect the existing data model and recommend the safest linking approach.

Where appropriate, add or use a nullable relationship from the Verbal Autopsy to the Death record.

The link must remain nullable because some Verbal Autopsies legitimately have no matching Death record.

Determine whether the correct relationship should be:

* One-to-one
* Many Verbal Autopsies to one Death record
* One Death record to many Verbal Autopsies
* Another relationship supported by the actual VACMS business process

Do not make this relationship mandatory without explicit confirmation.

Consider storing the following metadata:

* Linked Death record
* Matching method
* Match confidence score
* Match status
* Date matched
* Last date evaluated
* Matching algorithm version
* Whether the match was automatic or manually confirmed
* Ingestion or processing source
* Explanation of the fields that supported the match
* Explanation of contradictions or missing data
* Alternative candidate records
* User who manually reviewed or confirmed the link
* Reason for manual rejection
* Previous link history where a link changes

Suggested match statuses include:

* Automatically matched
* Manually confirmed
* Pending manual review
* Ambiguous
* Unmatched
* No Death record expected
* Rejected match
* Skipped
* Matching error

Do not overwrite an existing manually confirmed link unless an authorised user explicitly requests it.

## Matching lifecycle and re-evaluation

Define when a Verbal Autopsy should be evaluated or re-evaluated.

Potential triggers include:

* Initial creation
* Completion of PyODK ingestion
* Creation of the related Case Management event
* Creation of a Death record after the Verbal Autopsy already exists
* Update of relevant demographic fields
* Update of the Date of Death
* Update of the Case Management relationship
* Manual request for re-evaluation
* Retrospective management-command execution

Avoid unnecessary re-evaluation when unrelated fields change.

If a Death record is created after an unmatched Verbal Autopsy, determine whether VACMS should automatically re-evaluate possible links. Implement this only where it can be done safely and efficiently.

## Auditability

Every proposed or completed match must be explainable.

For each evaluated candidate, record or display:

* Candidate Death record identifier
* Overall confidence score
* Matching method
* Fields compared
* Individual field scores
* Exact matches
* Fuzzy matches
* Compatible values
* Missing values
* Contradictions
* Reasons for acceptance
* Reasons for rejection
* Rank among candidates
* Score difference from the next candidate
* Processing source
* Algorithm version

The process must support later review, correction, and audit.

## Error handling

Automatic matching must not make VA ingestion unreliable.

The implementation must:

* Separate ingestion success from matching success where appropriate.
* Avoid rolling back a valid Verbal Autopsy solely because no match exists.
* Clearly log unexpected matching failures.
* Avoid silently swallowing exceptions.
* Use transactions when applying links.
* Prevent partially written matching metadata.
* Allow failed matches to be safely retried.
* Avoid recursive save or signal loops.
* Avoid duplicate audit entries where the same evaluation is repeated without changes.

Where matching should occur after a database transaction commits, use an appropriate post-commit mechanism rather than executing against uncommitted or incomplete data.

## Performance and scalability

The implementation must:

* Avoid loading the full Death table into memory.
* Use indexed filtering fields where possible.
* Use `select_related`, `prefetch_related`, annotations, and query batching appropriately.
* Avoid N+1 query patterns.
* Support bulk retrospective processing.
* Avoid expensive fuzzy matching against unrestricted datasets.
* Provide recommendations for indexes required by candidate filtering.
* Be suitable for routine PyODK ingestion volumes.
* Include basic performance tests or query-count checks where practical.

## Configuration

Make key matching settings configurable where practical, including:

* Automatic-match threshold
* Manual-review threshold
* Maximum age tolerance
* Maximum date-of-death tolerance
* Candidate date range
* Minimum candidate-score separation
* Maximum number of candidates to score
* Whether auto-linking is enabled
* Whether ingestion errors should be raised or logged
* Matching algorithm version

Use the existing VACMS configuration pattern rather than creating an inconsistent mechanism.

## Safety controls

Implement the following protections:

* Default retrospective processing to dry-run mode.
* Do not update production records during the first execution.
* Produce a report of proposed matches before writing changes.
* Support processing a limited number of records for testing.
* Support filtering by date, district, facility, source, status, or identifier.
* Use database transactions when applying links.
* Make the operation idempotent.
* Do not duplicate links when rerun.
* Do not overwrite manually verified links.
* Log all changes.
* Allow rollback or reversal of automatically created links.
* Avoid destructive schema or data changes.
* Maintain backward compatibility with existing VA ingestion.
* Use feature flags or configuration to allow automatic linking to be disabled during deployment or troubleshooting.

## Required scaffolding and deliverables

Provide and implement all relevant scaffolding required for a production-ready feature, including:

1. A summary of the current VACMS model relationships relevant to Death records, Verbal Autopsies, Case Management events, facilities, households, and interviewers.
2. A documented inventory of every Verbal Autopsy ingestion pathway.
3. Any questions or ambiguities that must be resolved before implementation.
4. A proposed matching architecture.
5. A field-mapping table showing the relevant fields across Verbal Autopsy, Case Management event, and Death records.
6. A documented weighting and confidence-scoring model.
7. Reusable data-normalisation utilities.
8. Candidate-selection utilities.
9. A reusable single-record auto-link service.
10. A reusable bulk-matching service where appropriate.
11. A structured result class, dataclass, or equivalent result representation.
12. Django model changes where required.
13. Django migrations where required.
14. Appropriate database indexes.
15. Match-status and matching-method choices or enumerations.
16. Audit models or audit fields where appropriate.
17. Integration into PyODK Verbal Autopsy ingestion.
18. Integration into all relevant management-command ingestion pathways.
19. Integration into any other discovered VA creation or update pathway.
20. A retrospective Django management command.
21. Dry-run and apply modes.
22. CSV or structured reporting.
23. Application logging.
24. Error-handling and retry behaviour.
25. Feature-flag or configuration support.
26. Administrative or manual-review support where appropriate.
27. Automated unit tests.
28. Integration tests.
29. Management-command tests.
30. PyODK ingestion tests.
31. Tests for idempotency and repeated synchronisation.
32. Tests ensuring manual links are not overwritten.
33. Test fixtures or factories.
34. Developer documentation.
35. Operational instructions for development, staging, and production.
36. A deployment plan.
37. A rollback plan.
38. Recommendations for monitoring matching outcomes after deployment.
39. Recommendations for a future manual-review interface within VACMS.

## Required report fields

The retrospective command and matching service should be capable of producing a report containing:

* Verbal Autopsy identifier
* Verbal Autopsy database primary key
* Proposed Death record identifier
* Death record database primary key
* Case Management event identifier
* Match score
* Match method
* Match status
* Supporting fields
* Contradictory fields
* Missing fields
* Alternative candidate identifiers
* Alternative candidate scores
* Score separation from the second candidate
* Whether the record was linked
* Whether the record was previously linked
* Whether the existing link was manual
* Ingestion or processing source
* Matching algorithm version
* Processing timestamp
* Error details where applicable

## Test scenarios

At minimum, include tests for:

* A Verbal Autopsy linked through a Case Management event with fully consistent demographics
* A Case Management event whose linked Death record conflicts with the Verbal Autopsy
* An exact demographic match without a Case Management event
* A fuzzy name match with matching date of death, age, sex, and location
* A facility Verbal Autopsy with no corresponding Death record
* Two Death records with nearly identical match scores
* A match score above 90%
* A match score exactly equal to 90%
* A match score below 90%
* Missing dates
* Approximate age only
* Conflicting sex
* Conflicting dates of death
* Duplicate Death records
* A previously manually confirmed link
* A previously automatically linked record
* Re-running the management command after links have already been created
* Re-ingesting the same Verbal Autopsy through PyODK
* Loading a Verbal Autopsy through a management command
* Loading a Verbal Autopsy through another discovered ingestion pathway
* Updating an unrelated field without unnecessary re-matching
* Updating a relevant demographic field and triggering re-evaluation
* A Death record created after an unmatched Verbal Autopsy
* Matching service failure during ingestion
* Dry-run mode making no database changes
* Apply mode persisting only qualifying matches
* Bulk processing without excessive database queries
* Multiple candidate records where the top score is above 90% but insufficiently separated from the second candidate
* A manually rejected match remaining unlinked
* Automatic linking being disabled through configuration

## Questions to ask where necessary

Ask for clarification where the codebase does not clearly establish:

* The authoritative unique key for the Death record
* The authoritative unique identifier for a Verbal Autopsy
* How a Case Management event currently references a Verbal Autopsy
* How a Case Management event currently references a Death record
* Whether one Death record may have multiple Verbal Autopsies
* Whether one Verbal Autopsy may ever relate to multiple Death records
* Whether the relationship should be one-to-one, many-to-one, or one-to-many
* Whether facility Verbal Autopsies should later trigger creation of a Death record
* Which fields are authoritative when records conflict
* The acceptable tolerance for age and date differences
* The minimum acceptable score separation between candidates
* Whether manually reviewed links require approval by a specific role
* Whether match explanations should be stored in the database or only in audit reports
* Whether existing production data may already contain incorrect links
* Whether unmatched VAs should be automatically re-evaluated when a new Death record is added
* Whether matching should run synchronously during PyODK ingestion or after transaction commit
* Whether VACMS has an existing task queue or asynchronous processing mechanism
* Whether bulk import commands use `bulk_create`, which may bypass model save methods or signals
* Whether auto-linking must be enabled immediately in production or initially deployed behind a feature flag

Do not proceed with assumptions on these matters where they could materially affect data integrity.

## Expected working process

Follow this sequence:

1. Inspect the repository.
2. Document the current models and ingestion flows.
3. Identify uncertainties and ask targeted questions.
4. Propose the matching design.
5. Define the field mapping and scoring model.
6. Implement the reusable matching service.
7. Implement database changes and migrations.
8. Integrate the service into PyODK ingestion.
9. Integrate the service into all relevant management commands and other ingestion pathways.
10. Implement the retrospective management command.
11. Add tests.
12. Run the relevant test suite.
13. Run a limited dry-run against representative development or staging data.
14. Review false positives, false negatives, ambiguous matches, and unmatched records.
15. Document deployment, monitoring, and rollback procedures.

## Final implementation principle

Optimise for correctness, traceability, consistency, reversibility, and protection against false-positive linkage.

All Verbal Autopsy loading pathways must use the same reusable matching service.

A Verbal Autopsy that remains unmatched is preferable to a Verbal Autopsy linked to the wrong Death record.
