Baseline Census Dashboard: Detected vs Captured + KPI relabel + district list
Context
Three related changes to the analytics dashboards, driven by program requirements:

KPI label clarity — The Baseline Census overview KPI cards use the ambiguous labels "Today" / "This week". They should read "Last 24 hours" / "Last 7 days" (the home/Operations dashboard already uses these labels).
Detected vs Captured split — The pregnancies / pregnancy-outcomes / deaths figures currently show only captured events (separate Pregnancy / PregnancyOutcome / Death form submissions). Program staff also need the detected counts — the events households self-report inside the household census itself (Household.HH_46A, HH_47A, death_size). Surfacing both side-by-side shows the gap between what households reported and what was formally captured.
District filter coverage — The hardcoded geographic district dropdown is missing several active districts and is not alphabetically ordered.
Decisions confirmed with the user:

District-list change applies to the Baseline Census and Operations (home) dashboards only. The Outcomes dashboard uses a GeoJSON map selector (no hardcoded dropdown) and is out of scope.
"Detected" counts honor the same scoping as Captured: active time window, geographic filter, and the "Current Census" province exclusion.
Part A — District dropdown list (2 files)
Both dashboards share an identical hardcoded NOV_LOCATION_OPTIONS.district array. Replace the district list with the existing 5 districts plus the 7 new ones, alphabetically sorted:

Chirundu, Chisamba, Chongwe, Kalulushi, Kapiri Mposhi, Kazungula,
Luanshya, Lusaka, Mpongwe, Mumbwa, Namwala, Ndola
baseline_census_dashboard.js:429
home.js:304
Only the district array changes — province/constituency/ward arrays are untouched. The dropdown is flat (level-based, not province-cascading), so the values flow straight into location_value and are matched via the existing apply_text_geography_filter (district__icontains); districts without data simply show zero. No backend change needed.

Part B — "Detected" counts (backend)
"Detected" = sum of household self-reported counts, parsed from text fields on Household (household_census.py):

Pregnancies → HH_46A
Pregnancy outcomes → HH_47A
Deaths → death_size
These are TextFields, so values are parsed safely in Python (reuse the _safe_int pattern from dashboard_metrics.py:147, or add a small local helper) — never DB-cast arbitrary text.

B1. KPI windowed sums — home/services/nov.py
Add a helper _household_detected_windowed_sums(households_qs, field, start_dt, end_dt) that mirrors _db_windowed_counts but sums parsed field values instead of counting rows:

Annotate each household with _latest_ts via the existing _latest_text_expr(... CORE_EVENT_DATE_FIELDS).
Pull values_list("_latest_ts", field), parse each value with the safe-int helper, and bucket into the four windows using the existing _window_bounds(start_dt, end_dt) boundaries (today/week/last_30_days/total). One pass in Python — consistent with the table builder's existing Python aggregation style.
In get_national_operational_view_data, compute three detected sums from the already scoped+prefiltered households_qs (so location filter, census exclusion, and date prefilter all apply automatically) and add them to the returned kpis dict:

"pregnancies_detected": ...   "preg_outcomes_detected": ...   "deaths_detected": ...
BaselineCensusOverviewFilterDataView already returns the whole kpis dict, so no view change is required. The home/Operations dashboard ignores the extra keys harmlessly.

B2. Table per-geoid detected sums — va_analytics/views.py
Add _household_detected_counts_by_geoid(start_iso, end_iso, date_filter, target_rows) modeled on the existing _household_counts_by_geoid (views.py:1640):

Query Household filtered by submissiondate via date_filter, .values("cluster__name", "ea", "HH_46A", "HH_47A", "death_size").
Resolve each row's geoid with the existing _resolve_household_geoid helper.
Accumulate parsed sums per geoid → {geoid: {detected_deaths, detected_pregnancies, detected_preg_outcomes}}.
Part C — KPI cards UI (Baseline Census only)
File: _baseline_overview_kpis_component.html (home dashboard has its own component — do not touch it).

Relabel in every card: "Today" → "Last 24 hours", "This week" → "Last 7 days". (Leave the existing element IDs nov-kpi-today-* / nov-kpi-week-* unchanged to minimize churn — only the visible <span> text changes.) The census cards (Clusters / Households / People) keep their single-column layout, relabeled only.

Restructure the three event cards (Pregnancies, Pregnancy Outcomes, Deaths) into a 3-column grid:

Col 1 (no header): row labels "Last 24 hours", "Last 7 days", "Last 30 days", "Cumulative Total".
Col 2 header "Detected": new IDs nov-kpi-24h-pregnancies-detected, -week-...-detected, -30d-...-detected, -total-...-detected (and the preg-outcomes / deaths equivalents).
Col 3 header "Captured": the existing IDs (nov-kpi-today-pregnancies, etc.).
Add grid styles (e.g. .nov-kpi-split with grid-template-columns and the two column headers) to baseline_census_dashboard.css, where the other .nov-kpi-* rules live.

JS — baseline_census_dashboard.js
In updateNationalOperationalKpis (:516), read the new detected keys and populate the new detected IDs alongside the existing captured ones:

const pregnanciesDetected = kpis.pregnancies_detected || {};
setElementTextById("nov-kpi-24h-pregnancies-detected", pregnanciesDetected.today);
... (week / last_30_days / total, repeated for preg-outcomes and deaths)
Part D — Census table columns
File: _baseline_census_province_table.html

Rename headers and insert a "Detected" column immediately before each related captured column:

Detected Deaths → Captured Deaths (was "Total Deaths")
Detected Pregnancies → Captured Pregnancies (was "Total Pregnancies")
Detected Preg. Outcomes → Captured Preg. Outcomes (was "Total Preg. Outcomes")
Table grows from 15 to 18 columns. Updates needed in the template:

3 new <th> with sort links + the renamed captured headers.
3 new <td> in the tbody row, and in the tfoot ward-total + grand-total rows.
Empty-state colspan 15 → 18.
Grand-total <colgroup> (rebuild for 18 cols) and rebalance the width percentages.
Backend (BaselineCensusProvinceTableComponent, views.py:1799):

Rename row dict keys total_deaths/total_pregnancies/total_preg_outcomes → captured_deaths/captured_pregnancies/captured_preg_outcomes, and add detected_deaths/detected_pregnancies/detected_preg_outcomes from B2's detected_by_geoid in _build_census_rows.
Update _VALID_SORT_FIELDS and _SUM_FIELDS to drop the total_* names and add the six detected_* / captured_* names (all six are summable; coverage logic untouched).
CSV export _CSV_COLUMNS and the grand-total writerow in BaselineCensusTableCsvExport (views.py:2202): rename the three captured labels and insert the three detected columns in the same before-captured order so CSV matches the UI.
Files touched (summary)
va_explorer/static/js/baseline_census_dashboard.js — district list, KPI JS
va_explorer/static/js/home.js — district list
va_explorer/home/services/nov.py — detected windowed sums in KPIs
va_explorer/va_analytics/views.py — detected-by-geoid helper, table row keys, sort/sum field sets, CSV export
va_explorer/templates/va_analytics/components/_baseline_overview_kpis_component.html — labels + 3-col cards
va_explorer/templates/va_analytics/components/_baseline_census_province_table.html — table columns
va_explorer/static/css/baseline_census_dashboard.css — KPI split-grid styles
No model, migration, or cache-version changes. No new permissions.

Verification
Tests: pytest va_explorer/va_analytics/ va_explorer/home/ (shared venv at /srv/vacms/shared/venv; expect the known pre-existing failures from memory). Add/adjust a focused test asserting kpis contains the three *_detected keys and that the table row dicts expose detected_* + captured_*.
Manual: load /va_analytics/baseline-census-dashboard/ —
Overview: cards show "Last 24 hours" / "Last 7 days"; event cards show Detected/Captured columns; toggling time presets / the geographic dropdown / "Current Census" updates both columns consistently.
Census Table tab: new Detected columns sit before each Captured column; sorting on each new column works; CSV export header/rows match.
Geographic dropdown → District: list shows all 12 districts alphabetically, on both the Baseline Census and Operations (home) dashboards.
Detected math sanity: spot-check one EA — sum of HH_46A/HH_47A/death_size across its households equals the Detected cell.
Static deploy note (prod): JS/CSS/template static edits require collectstatic + python -m whitenoise.compress + hard refresh to appear (per project static-serving gotcha).
