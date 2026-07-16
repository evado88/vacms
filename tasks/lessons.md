# Lessons

## 2026-07-03 Lesson: Module-level date capture makes tests import-order dependent

**Mistake**: `va_explorer/home/va_trends.py` held `TODAY = pd.to_datetime(date.today())` (+ `START_MONTH`, `MONTHS`) as module-level constants. Adding the VASA dashboard/MSO test modules changed pytest-xdist loadscope scheduling; any test that hit a home view before `test_views.py::test_trends` imported the module at real time, so the time-machine-frozen (2021) test data never matched `date == TODAY` → `assert 0 == 2`.
**Why**: Import-time evaluation captures the date once per process. In production the same defect froze the "collected in last 24 hours" bucket at worker boot date.
**Fix**: Compute anchor dates per call (`_trend_window()` in `va_trends.py`, commit `4f86b43`).
**Rule**: If a test passes alone but fails in a group and per-test cache clearing is already in place (`va_explorer/conftest.py::_clear_cache`), suspect module-level state — grep `^[A-Z_]+ = .*(today|now)\(`. Never fix by reordering tests.
**Related Patterns**: LocMemCache persists per xdist-worker process (cleared by conftest); `scope_fingerprint` includes `user.pk`, so Django-cache collisions across tests are NOT the usual culprit.

## 2026-07-03 Lesson: Geo assertions need a production-shaped location tree

**Mistake**: `test_load_va_data_counts_coded_community_without_facility_location` built its province as a tree root; `load_va_data`'s coded-geo subqueries resolve province at `depth=2` / district at `depth=3` (production roots at a country), so the district was reported as the province.
**Why**: `LocationFactory` hardcodes a depth-1 root (`path="0001"`).
**Fix**: Root test trees at a country (`Location.add_root(name="Zambia", location_type="country")`) before adding province/district/facility (commit `6af7e2c`).
**Rule**: Any test asserting on `geographic_*_sums` / `map_*_sums` from `load_va_data` must build the tree under a country root.
