# VACMS Stage 7 Quality Gates + Responsive Testing Matrix

This matrix defines the required UI regression checks for the Outcomes design-system rollout and mobile responsiveness.

## Automated gates (run in CI/local)

- `python manage.py check`
- Template compile sweep: `get_template(...)` for all `va_explorer/templates/**/*.html`
- `python manage.py test --verbosity 1` (requires reachable PostgreSQL test DB)

## Breakpoints to validate

- `1440px`
- `1200px`
- `1024px`
- `992px`
- `768px`
- `480px`
- `375px`

## Representative page set by module

| Module | Representative pages |
|---|---|
| Outcomes dashboards | `va_analytics/outcomes_dashboard.html` (Pregnancy, Outcomes, Deaths, VA tabs) |
| Standalone dashboards | `va_analytics/pregnancy_dashboard.html`, `va_analytics/pregnancy_events_dashboard.html` |
| Data management | `va_data_management/index.html`, `va_data_management/households.html`, `va_data_management/pregnancies.html`, `va_data_management/pregnancy_outcomes.html`, `va_data_management/deaths.html` |
| Data cleanup | `va_data_cleanup/index.html` |
| CMS operations | `va_cms/event_list.html`, `va_cms/event_create.html`, `va_cms/event_schedule.html`, `va_cms/event_link.html`, `va_cms/event_complete.html` |
| Users/admin-like pages | `users/user_list.html`, `users/user_create.html`, `users/user_update.html` |
| Supervision/operations | `va_analytics/user_supervision_view.html`, `home/regional_operations.html` |
| Exports | `va_export/index.html` |

## Test checklist per breakpoint

For each representative page at each breakpoint validate:

1. Outcomes look-and-feel consistency:
   - shared card/header/filter/button/table classes applied
   - no legacy visual outliers in section spacing, radii, shadows, typography
2. Mobile usability:
   - touch targets remain usable
   - filters stack correctly under `768px`
   - action buttons remain visible and tappable
3. Layout integrity:
   - no page-level horizontal scroll
   - no overlapping cards/headers/filters
   - no clipping of titles/controls
4. Functional regression guardrails:
   - maps render and drill exactly as before (no behavior change)
   - charts render and update exactly as before (no data/algorithm change)
   - no console JS errors

## Runtime validation matrix (manual)

Mark each as `Pass`, `Fail`, or `N/A`.

| Page | 1440 | 1200 | 1024 | 992 | 768 | 480 | 375 | Notes |
|---|---|---|---|---|---|---|---|---|
| Outcomes dashboard tabs |  |  |  |  |  |  |  |  |
| Pregnancy events dashboard |  |  |  |  |  |  |  |  |
| Data management list pages |  |  |  |  |  |  |  |  |
| Data cleanup duplicates |  |  |  |  |  |  |  |  |
| CMS event list + forms |  |  |  |  |  |  |  |  |
| Users list + forms |  |  |  |  |  |  |  |  |
| Supervision + regional operations |  |  |  |  |  |  |  |  |
| Export page |  |  |  |  |  |  |  |  |

## Current execution status in this environment

- `python manage.py check`: **Pass**
- Template compile sweep (`158` templates): **Pass**
- `python manage.py test --verbosity 1`: **Blocked in sandbox** (PostgreSQL socket to `localhost:5432` not permitted)
- Browser/viewport verification at the 7 required widths: **Pending manual run in browser-enabled environment**

## Sign-off criteria

Stage 7 is complete when:

1. All matrix cells are marked `Pass` for representative pages at all required breakpoints.
2. No JS console errors are observed.
3. Maps/charts remain behaviorally unchanged (presentation-only refactor).
4. Automated gates pass in an environment with DB/test access.
