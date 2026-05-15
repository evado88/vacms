validate the following plan for adding the baseline census dashboards to the vacms

### Baseline Census Dashboard Clone (Overview + Trends + CSA)

#### Summary
Create a new Baseline Census dashboard under the `va_analytics` namespace that duplicates the existing Operational Dashboard implementation for only these tabs: `Overview`, `Trends`, and `CSA Supervision`. Wire the sidebar Baseline Census item to this new route, and introduce baseline-specific (renamed) template/CSS/JS/component assets and endpoint names.

#### Key Changes
1. **Routing and View Wiring**
- Add new analytics page route: `va_analytics:baseline-census-dashboard` at `/va_analytics/baseline-census-dashboard/`.
- Keep `va_analytics:dashboard` behavior unchanged (still redirects as today) for backward compatibility.
- Update sidebar Baseline Census link to point to `va_analytics:baseline-census-dashboard` and update active-state check to that URL name.
- Add baseline-prefixed analytics endpoints for the cloned tabs:
  - trends data endpoint
  - overview filter-data endpoint
  - overview map-data endpoint
  - overview KPI component endpoint
  - trends component endpoint
  - CSA filters component endpoint
  - CSA table component endpoint

2. **Baseline Page Template**
- Add `va_analytics/baseline_census_dashboard.html` as a dedicated dashboard shell.
- Include exactly 3 tabs with `Overview` default:
  - `overview`
  - `trends`
  - `csa_supervision`
- Copy the corresponding markup/slot structure from `home/index.html` for those tabs only.
- Point all `data-endpoint`/URL hooks to the new baseline-prefixed analytics endpoints.
- Load baseline-renamed assets (CSS/JS), while continuing to use shared infra scripts (`dashboard_loader.js`, `dashboard_tabs.js`).

3. **Renamed Baseline Assets**
- Add baseline-specific CSS clones (renamed from home equivalents):
  - dashboard styling clone
  - regional-operations styling clone
- Add baseline-specific JS clones (renamed from home equivalents):
  - overview/trends behavior script
  - CSA regional operations behavior script
- Scope JS to `data-shell="baseline-census"` and baseline endpoint hooks.
- Keep behavior parity with existing Overview/Trends/CSA logic; remove unrelated tab logic from cloned baseline JS (VA stats, MSO, rumor tabs).

4. **Renamed Baseline Component Templates/Views**
- Add baseline-prefixed component templates for:
  - overview KPI cards
  - trends panel
  - regional filters
  - CSA table
- Add view classes/wrappers for these baseline templates so endpoint names and assets are baseline-specific, while reusing existing data/query logic paths to avoid behavior drift.

#### Public Interfaces / Contract Changes
- **New page route**: `va_analytics:baseline-census-dashboard`.
- **New endpoint names (va_analytics namespace)** for baseline overview/trends/CSA components and data hooks (baseline-prefixed).
- **Sidebar navigation contract change**: Baseline Census menu item now targets the new baseline dashboard route instead of `va_analytics:dashboard`.

#### Test Plan
1. **Route + Render**
- Assert `GET /va_analytics/baseline-census-dashboard/` returns 200 for users with dashboard permission.
- Assert page contains only `overview`, `trends`, and `csa_supervision` tabs.
- Assert non-target tabs from home dashboard are absent (`va_statistics`, `mso_supervision`, rumor tabs).

2. **Sidebar Wiring**
- Assert rendered sidebar Baseline Census link href resolves to `va_analytics:baseline-census-dashboard`.
- Assert active class is applied when visiting the new baseline route.

3. **Endpoint/Component Smoke**
- Assert baseline-prefixed component endpoints return 200 and expected marker content (trends heading, CSA table/search controls, filters controls).
- Assert baseline trends/filter/map endpoints return expected response shape parity with existing implementation.

4. **Back-compat**
- Assert existing `va_analytics:dashboard` behavior remains unchanged (redirect target still valid).

#### Assumptions
- “Rename related assets accordingly” means creating baseline-prefixed template/CSS/JS/component assets and endpoint names, not renaming shared global infra scripts.
- Scope is limited to wiring via the **sidebar Baseline Census item**; top-nav/quick-action “Dashboard” links are not changed.
- No data model/schema changes are required; this is routing/template/asset/endpoint composition work.
