```text
You are working in the VACMS Django application.

Implement a new interactive drill-down chart in the Baseline Census Dashboard.

Objective
Add a new tab named “Census Chart” under the Overview section/tab of the Baseline Census Dashboard. This chart should provide an interactive combined bar and line visualization comparing estimated values, enumerated values, and coverage percentages for Population, Households, and Deaths across administrative levels.

Implementation Context
Use the existing Baseline Census Dashboard implementation patterns, naming conventions, route structure, frontend structure, API style, filters, permissions, and styling already used in VACMS. Do not introduce unnecessary architectural changes. Reuse existing dashboard utilities where appropriate.

Required UI Changes
1. Under the Overview tab of the Baseline Census Dashboard, add a new sub-tab named:

   Census Chart

2. The Census Chart tab should contain:
   - The same filters currently used in the Baseline Census Dashboard table tab.
   - A combined bar and line chart displayed below the filters row.
   - A visible drill-down breadcrumb or navigation control so users can understand their current level and return to a higher level.

3. The chart must compare the following three indicators:
   - Population
   - Households
   - Deaths

4. For each indicator, the chart must display:
   - Estimated value
   - Enumerated count
   - Coverage percentage

Chart Design Requirements
Use a combined bar and line chart with the following visual rules:

- Blue bars represent the estimated value.
- Red bars represent the enumerated count.
- Green line represents the coverage percentage.

The chart should show the Population, Household, and Deaths components for each geographical unit side by side.

At the top level, the chart should show all provinces contained in:

public.va_data_management_srseapopulationtarget

For each province, show grouped values for:
- Population estimate
- Population enumerated
- Population coverage %
- Household estimate
- Household enumerated
- Household coverage %
- Death estimate
- Death enumerated
- Death coverage %

Viewport and Responsiveness Requirements
The Census Chart should take up 100% of the available viewport width and height within the Census Chart tab.

Implementation requirements:
- The chart container should expand to fill the available tab content area.
- The chart canvas/SVG should use 100% width and 100% height of its parent container.
- The chart should resize automatically when the browser window changes size or when the user switches dashboard tabs.
- Avoid fixed chart heights unless they are used only as a safe fallback.
- Ensure the filters row remains visible above the chart, and the chart fills the remaining available vertical space below the filters.
- Use CSS layout best practices such as flexbox or grid to make the chart area responsive.
- Prevent overflow issues where possible, but allow controlled horizontal scrolling if the EA-level dataset is too wide to display clearly.
- If using Chart.js, configure maintainAspectRatio: false and ensure the parent container has an explicit responsive height.

Drill-Down Behaviour
Implement interactive drill-down functionality as follows:

1. Initial view:
   - Display all provinces from public.va_data_management_srseapopulationtarget.

2. Province click:
   - When the user clicks a province bar/group, update the chart to display all districts contained within that province.
   - The chart should continue showing Population, Household, and Deaths estimates, enumerated counts, and coverage.

3. District click:
   - When the user clicks a district bar/group, update the chart to display all constituencies contained within that district.

4. Constituency click:
   - When the user clicks a constituency bar/group, update the chart to display all wards contained within that constituency.

5. Ward click:
   - When the user clicks a ward bar/group, update the chart to display all EAs contained within that ward.

6. EA level:
   - EA should be the final drill-down level.
   - Clicking EA records should not trigger a deeper drill-down unless a future lower level exists.

7. Breadcrumb/back navigation:
   - Add a breadcrumb such as:
     Provinces > Copperbelt > Ndola > Kabushi > Chifubu Ward
   - Each breadcrumb item should be clickable and should reload the chart at that administrative level.
   - Also include a simple “Back” button or equivalent control for usability.

Data Requirements
The base geography and estimate values should come from:

public.va_data_management_srseapopulationtarget

Use the following mappings:

- province_name -> Province
- district_name -> District
- constituency_name -> Constituency
- ward_name -> Ward
- geoid -> EA
- population_target -> Population Estimate
- household_target -> Household Estimate

For Deaths:
- Use the best available death target/estimate field if one already exists in the srseapopulationtarget table or related baseline census target logic.
- If no death estimate field exists, derive or return a clearly named placeholder/null-safe value using existing VACMS conventions, but do not break the chart.
- Document the chosen approach in code comments.

Enumerated Counts
Calculate enumerated values using existing VACMS models/tables and the same logic used by the Baseline Census Dashboard table tab wherever possible.

At minimum, aggregate the following:

Population enumerated:
- Count of household members associated with the selected geography.

Households enumerated:
- Count of household records associated with the selected geography.

Deaths enumerated:
- Count of death records associated with the selected geography.

Use the existing location/geography mapping logic already used in the Baseline Census Dashboard table tab. Avoid duplicating inconsistent logic.

Coverage Calculations
Calculate coverage percentages as:

Population coverage:
population_enumerated / population_estimate * 100

Household coverage:
households_enumerated / household_estimate * 100

Death coverage:
deaths_enumerated / death_estimate * 100

Coverage calculation rules:
- Avoid division by zero.
- If the estimate is null, zero, or unavailable, return null or 0 consistently with existing dashboard conventions.
- Format coverage percentages clearly in tooltips and labels.
- Apply sensible rounding, preferably one decimal place.

Filtering Requirements
Copy the filters from the Baseline Census Dashboard table tab.

The filters should apply to the chart API and frontend state.

Filters should include all existing table-tab filters, including but not limited to any available:
- Current Baseline Census checkbox
- Province
- District
- Constituency
- Ward
- EA
- Date or period filters if already available
- Any other active filters used by the table tab

Default filter behaviour should match the table tab exactly.

If the “Current Baseline Census” checkbox exists and is checked by default in the table tab, it should also be checked by default in the Census Chart tab and should filter out data older than 3 months using the same existing logic.

Backend/API Requirements
Create or extend an API endpoint for the Census Chart data.

Suggested endpoint pattern:

/home/api/baseline-census/census-chart/

or follow the existing VACMS URL naming conventions if different.

The endpoint should accept:
- level: province | district | constituency | ward | ea
- province
- district
- constituency
- ward
- ea/geoid where applicable
- all copied filter parameters from the table tab

The endpoint should return JSON in a frontend-friendly structure, for example:

{
  "level": "province",
  "parent": null,
  "breadcrumbs": [
    {
      "level": "province",
      "label": "All Provinces",
      "value": null
    }
  ],
  "results": [
    {
      "label": "Central",
      "level": "province",
      "value": "Central",
      "province_name": "Central",
      "district_name": null,
      "constituency_name": null,
      "ward_name": null,
      "geoid": null,

      "population_estimate": 100000,
      "population_enumerated": 85000,
      "population_coverage": 85.0,

      "household_estimate": 20000,
      "households_enumerated": 18000,
      "household_coverage": 90.0,

      "death_estimate": 500,
      "deaths_enumerated": 410,
      "death_coverage": 82.0,

      "has_children": true
    }
  ]
}

The exact structure can be adjusted to match existing VACMS API conventions, but it must be clean, predictable, and documented enough for frontend use.

Backend Aggregation Requirements
Use efficient Django ORM queries wherever possible.

Ensure aggregation groups by the selected administrative level:

- level=province:
  group by province_name

- level=district:
  filter by selected province, group by district_name

- level=constituency:
  filter by selected province and district, group by constituency_name

- level=ward:
  filter by selected province, district, and constituency, group by ward_name

- level=ea:
  filter by selected province, district, constituency, and ward, group by geoid

The API should:
- Respect all filters.
- Avoid N+1 queries.
- Handle null administrative names safely.
- Sort results in a user-friendly order.
- Return empty datasets gracefully.
- Log or handle unexpected data inconsistencies without crashing the dashboard.

Frontend Requirements
Implement the chart using the charting library already preferred in VACMS. If the dashboard currently uses Chart.js, use Chart.js. If it uses another library consistently, follow that pattern.

The frontend should:
- Load chart data asynchronously.
- Apply filters when the user changes filters.
- Support drill-down by clicking a province, district, constituency, ward, or chart group.
- Update the breadcrumb after drill-down.
- Provide a loading state.
- Provide an empty-state message when no data is available.
- Provide an error-state message when the API fails.
- Avoid freezing the browser when the dataset is large.
- Destroy/recreate or update chart instances safely to avoid memory leaks.
- Debounce filter changes if needed.
- Resize correctly when the tab becomes visible, because charts rendered inside hidden tabs often calculate incorrect dimensions.
- Fill the available Census Chart tab width and height without requiring manual refresh.

Tooltip Requirements
Tooltips should clearly show:

- Geography name
- Indicator name
- Estimate
- Enumerated count
- Coverage percentage

Example tooltip:

Province: Central
Indicator: Population
Estimate: 100,000
Enumerated: 85,000
Coverage: 85.0%

Chart Layout Requirements
Because each geography has three indicators, make the chart readable by using one of the following approaches:

Preferred approach:
- Group the x-axis by geography.
- Within each geography, show Population, Households, and Deaths as sub-groups.
- For each subgroup, show blue estimate bar and red enumerated bar.
- Overlay the green coverage line using a secondary y-axis for percentage.

Alternative acceptable approach:
- Use a clean multi-series combined bar/line chart where each geography has grouped bars for estimates and enumerated counts, with coverage shown on the secondary y-axis.
- Ensure labels and tooltips make the indicator distinction clear.

The final implementation must be readable and usable even when many districts, wards, or EAs are displayed.

Responsive Layout Requirements
Use a robust responsive layout for the Census Chart tab.

The recommended layout is:

- The Census Chart tab container should be a vertical flex or grid layout.
- The filters row should occupy only the height it needs.
- The breadcrumb/navigation row should occupy only the height it needs.
- The chart area should flex-grow and fill all remaining available vertical and horizontal space.
- The chart canvas/SVG should stretch to the full width and height of the chart area.
- The page should not introduce unnecessary nested scrollbars.
- For very wide EA-level data, horizontal scrolling may be enabled only within the chart area.
- The chart should remain readable on common desktop screen sizes.

If the existing dashboard has a common card, panel, or responsive chart container class, reuse or extend that class rather than creating a separate inconsistent layout system.

Performance and Usability Best Practices
- Limit unnecessary full-page reloads.
- Reuse existing dashboard filter state if available.
- Keep API responses compact.
- Use server-side aggregation rather than sending raw rows to the browser.
- Use pagination, horizontal scrolling, responsive resizing, or label rotation where needed for many EAs.
- Avoid hardcoded province, district, constituency, ward, or EA names.
- Avoid hardcoded assumptions about Central and Copperbelt only unless the existing dashboard deliberately limits the Baseline Census Dashboard to those provinces.
- Preserve existing dashboard functionality.
- Avoid expensive frontend data transformations on every render.
- Avoid deep-cloning large chart datasets.
- Cache reusable filter options where appropriate.
- Ensure chart redraws are triggered only when necessary.

Files to Inspect
Before implementation, inspect the following areas and adapt to the actual project structure:

- Baseline Census Dashboard templates
- Baseline Census Dashboard overview tab implementation
- Baseline Census Dashboard table tab template
- Baseline Census Dashboard JavaScript files
- Baseline Census Dashboard CSS files
- Existing dashboard API views and URL routes
- Existing srseapopulationtarget model or raw table mapping
- Existing household, household member, pregnancy, pregnancy outcome, and death aggregation logic
- Existing “Current Baseline Census” filtering logic
- Existing chart implementation patterns in VACMS
- Existing responsive chart/card layout classes
- Existing tab-switch handling logic for dashboard charts

Testing Requirements
Add or update tests where the project structure supports them.

At minimum, test:

1. The Census Chart tab renders in the Baseline Census Dashboard.
2. The chart API returns province-level data by default.
3. The chart API returns district-level data when a province is selected.
4. The chart API returns constituency-level data when a district is selected.
5. The chart API returns ward-level data when a constituency is selected.
6. The chart API returns EA-level data when a ward is selected.
7. Coverage calculations avoid division-by-zero errors.
8. Filters from the table tab are respected.
9. The Current Baseline Census filter behaves the same as in the table tab.
10. Empty datasets return a safe response and do not crash the frontend.
11. The chart container fills the available Census Chart tab width and height.
12. The chart resizes correctly after browser resize and tab switching.

Acceptance Criteria
The implementation is complete when:

- A “Census Chart” tab appears under the Overview tab of the Baseline Census Dashboard.
- The Census Chart tab has the same filters as the table tab.
- The chart displays estimates, enumerated values, and coverage for Population, Households, and Deaths.
- Blue bars represent estimates.
- Red bars represent enumerated counts.
- Green line represents coverage percentage.
- The chart initially displays provinces from public.va_data_management_srseapopulationtarget.
- Clicking a province drills down to districts.
- Clicking a district drills down to constituencies.
- Clicking a constituency drills down to wards.
- Clicking a ward drills down to EAs.
- Breadcrumb/back navigation works.
- Filters update the chart correctly.
- The Census Chart fills 100% of the available viewport width and height within the tab, while keeping the filters visible above it.
- The chart resizes correctly when the browser window changes size.
- The chart resizes correctly when switching away from and back to the Census Chart tab.
- The chart handles empty and large datasets gracefully.
- Existing Baseline Census Dashboard functionality remains intact.
- Existing table tab behaviour is not broken.
- Code follows VACMS conventions and is maintainable.

Implementation Notes
Be careful with naming consistency. The user-facing label should be:

Census Chart

Use formal and clear variable names. Prefer reusable backend helper functions for aggregation and coverage calculations rather than repeating logic across levels.

Where existing VACMS code already solves a related problem, reuse or adapt it instead of creating parallel inconsistent logic.

Do not hardcode administrative names, chart values, or filter options. The chart must be data-driven from the database and existing VACMS filter logic.
```
