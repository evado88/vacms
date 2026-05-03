# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Local development (no Docker)
```shell
# Install dependencies
pip install -r requirements/local.txt

# Database setup
createdb vae_cms_db -U postgres && cp .env.template .env
python manage.py migrate

# Bootstrap permissions and first admin
python manage.py initialize_groups
python manage.py seed_admin_user admin@example.com --password <password>

# Run server
python manage.py runserver   # accessible at localhost:8000
```

### Testing
```shell
pytest                                     # all tests (parallel via pytest-xdist)
pytest va_explorer/home/tests/             # single app
pytest va_explorer/home/tests/test_views.py::TestClass::test_method  # single test
coverage run -m pytest && coverage report  # with coverage (min 60%)
```

The test settings module is `config.settings.test` (set via `--ds` in `pyproject.toml`). Tests use `--reuse-db` and run in parallel with `--dist=loadscope`.

### Linting / formatting
```shell
pre-commit install && pre-commit run --all-files   # ruff + black
```

Ruff is configured in `pyproject.toml`; Black line length is 88. Migrations are excluded from both.

### Cause-of-death coding (requires Docker)
```shell
docker-compose up --build && python manage.py run_coding_algorithms
```

### Other useful management commands
| Command | Location | Purpose |
|---|---|---|
| `load_locations` | `va_data_management` | Import location tree |
| `load_va_csv` | `va_data_management` | Bulk-import VA records from CSV |
| `seed_demo_users` | `users` | Create demo users for each role |
| `import_from_odk` | `va_data_management` | Pull VAs from ODK Central |

## Architecture

### App layout
All Django apps live under `va_explorer/`. The settings split is `config/settings/{base,local,production,test}.py`.

| App | Purpose |
|---|---|
| `home` | Landing page dashboards (operational / surveillance views); dashboard cache pre-warming |
| `users` | Custom `User` model (email auth via allauth), group/permission management, feedback module |
| `va_analytics` | Read-only analytics dashboards and REST API endpoints consumed by frontend JS |
| `va_data_management` | Core data models, ODK/Kobo import, cause-coding pipeline |
| `va_data_cleanup` | Data quality issue detection and correction |
| `vacms` | Case Management System — `Event` lifecycle tracking for deaths, pregnancies, outcomes |
| `va_export` | CSV / API data export |
| `dhis_manager` | DHIS2 export integration |
| `va_admin` | Admin panel, management command registry, alert management |
| `utils` | Shared mixins, middleware, profiling helpers |

### Data models
`va_data_management/models/` is split into one file per domain — the top-level `models.py` re-exports everything via `from .models import *`.

Key models:
- **`VerbalAutopsy`** — 200+ WHO form fields; extends `SoftDeletionModel` (`va_explorer/models.py`). Use `.objects` for live records, `.all_objects` to include soft-deleted.
- **`Location`** — `MP_Node` (django-treebeard materialized path). Used for all geographic scoping. Cache is version-keyed via `location_tree:version` in Redis.
- **`Death` / `Pregnancy` / `PregnancyOutcome` / `Household`** — operational forms linked to Location.
- **`Event`** (vacms) — case lifecycle; uses `on_delete=RESTRICT` on its FKs to `Death`, `VerbalAutopsy`, `Pregnancy`, and `PregnancyOutcome` — deleting those records will fail if an Event references them.
- **`CauseOfDeath` / `CauseCodingIssue`** — output of the InterVA5 / pyCrossVA coding pipeline.

### Access control
Two-layer system:
1. **Group permissions** — four groups (Admins, Data Managers, Field Workers, Supervisors) defined in `fixtures/group.json`, bootstrapped with `initialize_groups`. Custom permissions live on `va_analytics` (e.g. `view_pii`, `download_data`, `supervise_users`).
2. **Location scoping** — `User.location_restrictions` is an M2M to `Location`. Every queryset that should be scoped goes through `restrict_queryset_to_user_locations()` in `va_data_management/utils/location_access.py`. The helper also traverses ancestors/descendants and caches the result per-request on the user object.

All protected views use `CustomAuthMixin` (handles unauthenticated + invalid-password redirects) from `va_explorer/utils/mixins.py`, typically combined with Django's `PermissionRequiredMixin`. Use `LocationRestrictedQuerySetMixin` (same file) when a view's queryset must respect location scoping.

### Dashboard / API pattern
Dashboards are **server-rendered HTML shells** that load data from REST API endpoints on the frontend. The API endpoints are Django REST Framework `APIView` subclasses in `va_analytics/views.py` and `home/views.py`, routed under `va_analytics/` URLs.

The home dashboard has a Redis-backed pre-warm cache (5-minute Celery Beat task) keyed on `HOME_DASHBOARD_PREWARM_KEY`. Cache invalidation is version-keyed — bump `home_dashboard_fastpath:version` to invalidate all home dashboard cache entries.

Location tree cache follows the same pattern: bump `location_tree:version` (via `bump_location_tree_version()`) whenever `Location` records change.

### Celery
Broker and result backend: Redis (`CELERY_BROKER_URL`). Scheduler: `django_celery_beat` (database-backed). Key scheduled work:
- ODK pull: every ~15 min (configurable per form in `ODK_PULL_FORMS` / `ODK_PULL_SCHEDULES` in `base.py`)
- Home dashboard pre-warm: every 5 min
- Cause coding: daily

Long-running import and coding jobs have a 45-min soft / 50-min hard time limit.

### External services
| Service | Default port | Purpose |
|---|---|---|
| InterVA5 | 5002 | Cause of death coding algorithm |
| pyCrossVA | 5001 | VA questionnaire cross-mapping |
| ODK Central | configured via env | Field data collection |
| DHIS2 | configured via env | Health information system export |

### Frontend
Bootstrap 4 + plain JS. Static files served by WhiteNoise. No build step — CSS/JS live in `va_explorer/static/`. Templates are in `va_explorer/templates/`.
