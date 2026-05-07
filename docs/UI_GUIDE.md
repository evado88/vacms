# VACMS UI Guide (Stage 1)

Source of truth for the modern VACMS design language is the Outcomes dashboards implementation.

## Canonical source files

- `va_explorer/templates/va_analytics/outcomes_dashboard.html`
- `va_explorer/templates/va_analytics/partials/_pregnancies_dashboard.html`
- `va_explorer/templates/va_analytics/partials/_pregnancy_outcomes_dashboard.html`
- `va_explorer/templates/va_analytics/partials/_deaths_dashboard.html`
- `va_explorer/templates/va_analytics/partials/_verbal_autopsies_dashboard.html`
- `va_explorer/static/css/outcomes_dashboard.css`
- `va_explorer/static/css/dashboard.css` (base primitives used by outcomes)
- `va_explorer/static/js/dashboard_shell_tabs.js`
- `va_explorer/static/js/pregnancy_dashboard.js`
- `va_explorer/static/js/outcomes_dashboard.js`
- `va_explorer/static/js/deaths_dashboard.js`

## A) Layout primitives

- Shell:
  - `.row.mt-4.dashboard-shell[data-shell="outcomes"]`
  - `.dashboard-tab-panel[data-tab-panel]` inside `#outcomesTabsContent`
- Dashboard root container:
  - `main.outcomes-dashboard-root`
  - IDs: `#pregnancyDashboardApp`, `#outcomesDashboardApp`, `#deathsDashboardApp`, `#dashboardApp`
- Section wrapper:
  - `.sectionContainer`
  - Common section variants: `.panelView`, `.mapView`, `.trendView`, `.topCausesView`, `.demographicsView`
- Spacing rhythm:
  - Uses `--od-gap` token and section-level spacing with consistent chart/card padding.
  - Tabs and panes use uniform outer shell spacing and borders.
- Responsive collapse rules:
  - Two-column section rows use `.po-split-row`; collapse to single column in `@media (max-width: 992px)`.
  - Filter rows are horizontal and scrollable where needed (`overflow-x` behavior on `.filtersRow`).

## B) Card system

- Card wrapper primitives:
  - `.sectionContainer` (main section card)
  - `.highlight` (KPI/stat card)
  - Optional `.dashboard-card` where used in VA tab
- Header/body structure:
  - KPI card header: `.highlight h3`
  - KPI card value/body: `.highlight p`
  - Card groups: `.highlightsContainer`
- Visual tokens:
  - `--od-card`, `--od-border`, `--od-shadow`, `--od-shadow-hover`, `--od-radius`
- Header controls layout:
  - `.dashboard-section-head` with left title and right controls/toggles.

## C) Typography scale

- Page/shell title:
  - Outcomes page heading in shell; section titles use `.outcomes-dashboard-root h2`.
- Section titles:
  - `h2` in each `.sectionContainer`, with uniform weight and spacing.
- KPI numbers and labels:
  - Labels: `.highlight h3`
  - Values: `.highlight p` (bold, higher visual weight)
- Notes/helper/muted:
  - `.dashboard-note` for explanatory text under filters
  - `.po-empty-state` for empty states

## D) Controls

- Filter bar:
  - Form class: `.controls`
  - Row class: `.filtersRow`
  - Grouping classes:
    - `.mainFiltersContainer`
    - `.betweenDatesContainer`
    - `.customDatesContainer`
    - `.formBtnsContainer`
- Buttons:
  - Reset: `.button.resetBtn`
  - Update action: `.button.updateDataBtn` (where applicable)
- Toggle/radio styling:
  - `.chart-mode-toggle`
  - Right-aligned variant: `.chart-mode-toggle--right`
- Date input compact behavior:
  - `input[type="date"].is-icon-only` (set by JS when narrow)
- Tabs:
  - `#outcomesTabs .nav-link` + active state.

## E) Data presentation primitives

- KPI tiles:
  - `.highlightsContainer > .highlight > h3 + p`
  - Deaths variants: `.deaths-summary-cards`, `.deaths-summary-cards-secondary`, `.deaths-signal-card`
- Chart wrappers:
  - `.chartsContainer`
  - `.chart-canvas-wrap` and `.chart-canvas-wrap.compact`
  - `.topCauseLineChartContainer` where line charts are embedded
- Table styling:
  - Outcomes dashboard is chart-heavy; table styling follows global dashboard table styles (`dashboard.css` and shared system CSS), not a bespoke outcomes table class.
- Empty/loading:
  - Empty state element per component: `*.po-empty-state[hidden]`
  - Loading text pattern: `.loadingText` (used in VA dashboard controls)

## JS wiring patterns tied to the UI primitives

- Tab shell behavior:
  - `dashboard_shell_tabs.js` toggles `.dashboard-tab-panel` visibility and emits:
    - `dashboard:refresh-component`
    - `dashboard:refresh-tab`
- Component discovery:
  - `data-component="..."` attributes on sections/charts/forms
- Endpoint wiring:
  - Root dashboard `<main>` stores API endpoints in `data-*-endpoint` attributes.
- Empty-state toggling:
  - JS helpers set `hidden` on `*.po-empty-state`.
- Filter/URL sync pattern:
  - Controls update query params; dashboards refresh in-place (no full page reload).

## Partials to reuse for UI consistency

- Shell:
  - `va_explorer/templates/va_analytics/outcomes_dashboard.html`
- Pregnancy:
  - `_pregnancies_dashboard.html`
  - `_pe_summary_cards.html`, `_pe_filters.html`, `_pe_map.html`, `_pe_trend.html`, `_pe_gestational_age.html`, `_pe_anc_visits.html`
- Pregnancy outcomes:
  - `_pregnancy_outcomes_dashboard.html`
  - `_po_summary_cards.html`, `_po_filters.html`, `_po_map.html`, `_po_trend.html`, `_po_birth_outcomes.html`, `_po_place_of_birth.html`, `_po_gestational_age.html`, `_po_anc_visits.html`, `_po_kpis.html`
- Deaths:
  - `_deaths_dashboard.html`
  - `_deaths_summary.html`, `_deaths_map.html`, `_deaths_trend.html`, `_deaths_age_sex.html`, `_deaths_place.html`, `_deaths_timeliness.html`
- Verbal autopsies:
  - `_verbal_autopsies_dashboard.html` (already aligned to the same section/card/filter primitives)
