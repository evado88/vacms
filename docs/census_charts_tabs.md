Copy and paste the prompt below into Codex or Claude:

```text
You are working in the VACMS codebase. Update only the Baseline Census Dashboard implementation, specifically the Census Chart tab.

Objective:
Enhance the existing Census Chart tab under the Baseline Census Dashboard by adding internal sub-tabs for the following chart categories:

1. Population
2. Households
3. Deaths
4. Pregnancies
5. Pregnancy Outcomes

Important constraint:
Do not change, remove, or regress any existing implementation in the other Baseline Census Dashboard tabs. The Overview, Table, and any other existing tabs must continue working exactly as they currently do.

Implementation requirements:

1. Preserve the current Baseline Census Dashboard structure
- Keep the main Baseline Census Dashboard tabs unchanged.
- Only modify the content rendered inside the existing Census Chart tab.
- The Census Chart tab should become a container for the new sub-tabbed chart layout.

2. Add sub-tabs inside the Census Chart tab
- Add a clear and user-friendly sub-tab navigation section inside the Census Chart tab.
- The sub-tabs should be:
  - Population
  - Households
  - Deaths
  - Pregnancies
  - Pregnancy Outcomes
- The Population sub-tab should be active by default when the Census Chart tab is opened.
- Switching between sub-tabs should not reload the whole page.
- Sub-tab switching should be handled cleanly using the project’s existing frontend conventions.

3. Preserve existing Census Chart behavior where applicable
- If the current Census Chart implementation already contains a chart, migrate or place that chart under the most appropriate sub-tab.
- If the existing chart compares estimated, enumerated, and coverage values for Population, Households, and Deaths, refactor the charting implementation so each indicator has its own focused sub-tab.
- Avoid duplicating large blocks of chart code unnecessarily. Use reusable helper functions/components where possible.

4. Expected chart behavior per sub-tab
Each sub-tab should have its own chart container and should be prepared to display indicator-specific census performance data.

Population tab:
- Show population-related estimated, enumerated, and coverage metrics.
- Use the existing Baseline Census Dashboard filters.

Households tab:
- Show household-related estimated, enumerated, and coverage metrics.
- Use the existing Baseline Census Dashboard filters.

Deaths tab:
- Show deaths-related estimated, enumerated, and coverage metrics.
- Use the existing Baseline Census Dashboard filters.

Pregnancies tab:
- Show pregnancy-related census metrics.
- Use the existing Baseline Census Dashboard filters.
- Where no estimate or target exists, handle the chart gracefully by showing enumerated counts and any available derived coverage only if supported by existing data.

Pregnancy Outcomes tab:
- Show pregnancy outcome-related census metrics.
- Use the existing Baseline Census Dashboard filters.
- Where no estimate or target exists, handle the chart gracefully by showing enumerated counts and any available derived coverage only if supported by existing data.

5. Filters
- Reuse the existing filters already implemented for the Census Chart tab or copied from the Baseline Census Dashboard table tab.
- Do not create inconsistent filter logic.
- Filter selections should apply to the active chart sub-tab.
- If feasible, preserve filter state when users switch between Population, Households, Deaths, Pregnancies, and Pregnancy Outcomes sub-tabs.

6. Frontend best practices
- Keep JavaScript modular and avoid global namespace pollution.
- Use event delegation or clearly scoped event listeners.
- Prevent duplicate chart initialization when users switch between tabs repeatedly.
- Destroy, update, or reuse chart instances properly to avoid memory leaks.
- Ensure chart rendering only occurs when the relevant tab/sub-tab container is visible.
- Use clear naming conventions such as:
  - censusChartTabs
  - censusPopulationChart
  - censusHouseholdsChart
  - censusDeathsChart
  - censusPregnanciesChart
  - censusPregnancyOutcomesChart
- Avoid hardcoding duplicated API calls where a reusable function can be used.

7. Backend/API best practices
- If the existing Census Chart endpoint already supports multiple indicators, extend it cleanly to support the new sub-tab indicators.
- If needed, add an `indicator` query parameter with supported values such as:
  - population
  - households
  - deaths
  - pregnancies
  - pregnancy_outcomes
- Validate the indicator server-side and return a clear error for unsupported values.
- Keep existing endpoint behavior backward-compatible where possible.
- Do not break any existing frontend calls that rely on the current Census Chart API response shape.
- Use efficient ORM queries and avoid N+1 query patterns.
- Reuse existing aggregation logic where possible.

8. Data handling expectations
- Population, household, and death charts should continue using the relevant estimated, enumerated, and coverage values already available in the Baseline Census Dashboard implementation.
- Pregnancy and pregnancy outcome charts should use available model/table counts already used elsewhere in the Baseline Census Dashboard.
- If estimates or targets are unavailable for pregnancies or pregnancy outcomes, the UI should not fail. Instead:
  - show enumerated counts only, or
  - show a clear “No target available” state for estimate/coverage values, depending on the existing design pattern.
- Null, missing, or zero denominator values must be handled safely.
- Coverage percentages must not produce divide-by-zero errors.

9. UI/UX requirements
- The sub-tabs should visually fit the existing VACMS dashboard style.
- The layout should remain responsive.
- The chart area should continue to use the available viewport efficiently.
- Use consistent spacing, typography, and colors with the existing dashboard.
- The active sub-tab should be visually clear.
- Empty states should be professional and informative.
- Loading states should be shown while chart data is being fetched.
- Error states should be handled gracefully without breaking the dashboard.

10. Testing and validation
Update or add tests where appropriate to confirm that:
- The Baseline Census Dashboard still renders successfully.
- The existing main dashboard tabs still work.
- The Census Chart tab renders the new sub-tabs.
- Population is the default active Census Chart sub-tab.
- Each Census Chart sub-tab can be selected without JavaScript errors.
- API calls for each supported indicator return valid payloads.
- Unsupported indicator values are handled safely.
- Existing Census Chart behavior is not broken.
- Filters still apply correctly to chart data.

11. Files to inspect
Before implementing, inspect the existing Baseline Census Dashboard implementation and identify the relevant files, likely including:
- Dashboard template files containing the Baseline Census Dashboard tabs.
- Census Chart template/partial files.
- JavaScript files responsible for Baseline Census Dashboard chart rendering.
- CSS files for dashboard styling.
- Django views or API endpoints serving Baseline Census Dashboard data.
- URL route definitions for dashboard APIs.
- Existing tests for dashboard rendering or payloads.

12. Acceptance criteria
The implementation is complete when:
- The Baseline Census Dashboard has an unchanged main tab structure.
- The Census Chart tab contains five sub-tabs:
  - Population
  - Households
  - Deaths
  - Pregnancies
  - Pregnancy Outcomes
- Population is active by default.
- Selecting each sub-tab displays or prepares the correct indicator-specific chart area.
- Existing filters remain available and functional.
- Other Baseline Census Dashboard tabs remain unchanged.
- Existing tests pass.
- New or updated tests cover the new sub-tab behavior.
- No JavaScript console errors occur when switching between tabs or filters.
- The implementation follows existing VACMS coding, template, JavaScript, and styling conventions.

Deliverables:
- Implement the sub-tabbed Census Chart layout.
- Refactor or extend frontend chart rendering as needed.
- Extend backend chart data support only if required.
- Add or update tests.
- Keep the implementation backward-compatible and limited to the Census Chart tab.
```
