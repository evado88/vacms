GRAPH_OPTIONS = {
  responsive: true,
  scales: { y: { beginAtZero: true } },
  plugins: { legend: { display: false } }
};

BORDER_COLOR = "#037BFE"
const chartInstances = new WeakMap();
let novFiltersBound = false;
let overviewInitialized = false;
let trendsInitialized = false;
let homeDataPromiseBySection = {};
let homeDataCacheBySection = {};
let firstRenderMeasured = false;
const tabWarmCache = new Set();
let homeOverviewMapController = null;
let novFilterRequestId = 0;

const SHELL_SELECTOR = ".dashboard-shell[data-shell='baseline-census']";

const perfNow = () => (
  window.performance && typeof window.performance.now === "function"
    ? window.performance.now()
    : Date.now()
);
const perfLog = (name, startMs, meta) => {
  const durationMs = perfNow() - startMs;
  if (meta) {
    console.log(`[perf][baseline-census] ${name} ${durationMs.toFixed(2)}ms`, meta);
  } else {
    console.log(`[perf][baseline-census] ${name} ${durationMs.toFixed(2)}ms`);
  }
};

const getDashboardLoader = () => window.DashboardLoader || null;
const hasJqueryAjax = () => typeof window !== "undefined" && window.$ && typeof window.$.ajax === "function";
const getTrendsEndpoint = (section = "model") => {
  const shell = document.querySelector(SHELL_SELECTOR);
  const endpoint = shell?.dataset?.trendsEndpoint || "";
  const base = endpoint || "/trends/";
  const separator = base.includes("?") ? "&" : "?";
  return `${base}${separator}section=${encodeURIComponent(section)}`;
};

const fetchSlotHtmlFallback = async (slot) => {
  const endpoint = (slot && slot.dataset && slot.dataset.endpoint) ? slot.dataset.endpoint : "";
  if (!endpoint) return;
  const response = await fetch(endpoint, {
    method: "GET",
    headers: { "X-Requested-With": "XMLHttpRequest" },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error(`Failed to load baseline-census slot from ${endpoint}`);
  }
  slot.innerHTML = await response.text();
  slot.dispatchEvent(
    new CustomEvent("dashboard:component-loaded", {
      bubbles: true,
      detail: { endpoint },
    })
  );
}

const loadSlotOnce = async (slotId) => {
  const slot = document.getElementById(slotId);
  const loader = getDashboardLoader();
  if (!slot) return;
  try {
    if (loader && typeof loader.loadComponentOnce === "function") {
      await loader.loadComponentOnce(slot);
      return;
    }
    await fetchSlotHtmlFallback(slot);
  } catch (error) {
    console.error(`[baseline-census] failed to load slot: ${slotId}`, error);
  }
}

const invalidateScope = (scopeKey) => {
  const loader = getDashboardLoader();
  if (!loader || typeof loader.invalidateComponentsByScope !== "function") return;
  loader.invalidateComponentsByScope(scopeKey);
}

const clearTrendsCache = () => {
  homeDataPromiseBySection = {};
  homeDataCacheBySection = {};
}

const setSingleTrendChart = (canvas, x, y) => {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const existingChart = chartInstances.get(canvas);
  if (existingChart) {
    existingChart.data.labels = x;
    if (existingChart.data.datasets[0]) {
      existingChart.data.datasets[0].data = y;
      existingChart.data.datasets[0].borderColor = BORDER_COLOR;
    } else {
      existingChart.data.datasets = [{
        data: y,
        borderColor: BORDER_COLOR,
      }];
    }
    existingChart.update("none");
    return;
  }

  const chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: x,
      datasets: [{
        data: y,
        borderColor: BORDER_COLOR,
      }]
    },
    options: GRAPH_OPTIONS,
  });
  chartInstances.set(canvas, chart);
}

const toNumericSeries = (values) => {
  if (!Array.isArray(values)) return [];
  return values.map((value) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : 0;
  });
}

const ensureCumulativeNonDecreasing = (values) => {
  const numericValues = toNumericSeries(values);
  let hasDip = false;
  for (let index = 1; index < numericValues.length; index += 1) {
    if (numericValues[index] < numericValues[index - 1]) {
      hasDip = true;
      break;
    }
  }
  if (!hasDip) return numericValues;

  let runningTotal = 0;
  return numericValues.map((value) => {
    runningTotal += value;
    return runningTotal;
  });
}

const selectedNovPreset = () => {
  if (document.getElementById("novTime30")?.checked) return "30";
  if (document.getElementById("novTime7")?.checked) return "7";
  if (document.getElementById("novTime24")?.checked) return "24";
  return "all";
}

const toDateString = (dateObj) => {
  const pad = (n) => String(n).padStart(2, "0");
  return `${dateObj.getFullYear()}-${pad(dateObj.getMonth() + 1)}-${pad(dateObj.getDate())}`;
}

const applyPresetToDatetimeInputs = (preset) => {
  const startInput = document.getElementById("novStartDatetime");
  const endInput = document.getElementById("novEndDatetime");
  if (!startInput || !endInput) return;

  if (preset === "all") {
    startInput.value = "";
    endInput.value = "";
    return;
  }

  const now = new Date();
  const start = new Date(now);
  if (preset === "30") start.setDate(start.getDate() - 30);
  if (preset === "7") start.setDate(start.getDate() - 7);
  if (preset === "24") start.setHours(start.getHours() - 24);
  startInput.value = toDateString(start);
  endInput.value = toDateString(now);
}

const clearPresetSelection = () => {
  ["novTimeAll", "novTime30", "novTime7", "novTime24"].forEach((id) => {
    const input = document.getElementById(id);
    if (input) input.checked = false;
  });
}

const NOV_LOCATION_OPTIONS = {
  national: [],
  province: ["Central", "Copperbelt", "Eastern", "Southern", "Lusaka", "Western"],
  district: ["Chirundu", "Chongwe", "Kazungula", "Lusaka", "Namwala"],
  constituency: [
    "Chirundu Constituency",
    "Chongwe Constituency",
    "Kazungula Constituency",
    "Lusaka Central Constituency",
    "Namwala Constituency",
  ],
  ward: [
    "Chirundu Ward",
    "Kanakantapa Ward",
    "Kazungula Ward",
    "Kanyama Ward",
    "Namwala Central Ward",
  ],
}

const updateNovLocationValueOptions = () => {
  const levelSelect = document.getElementById("novLocationLevel");
  const valueSelect = document.getElementById("novLocationValue");
  if (!levelSelect || !valueSelect) return;

  const level = levelSelect.value || "national";
  const options = NOV_LOCATION_OPTIONS[level] || [];
  const disableValue = level === "national" || options.length === 0;

  valueSelect.innerHTML = "";
  if (disableValue) {
    valueSelect.disabled = true;
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "All National";
    valueSelect.appendChild(option);
    return;
  }

  valueSelect.disabled = false;
  options.forEach((item, index) => {
    const option = document.createElement("option");
    option.value = item;
    option.textContent = item;
    if (index === 0) option.selected = true;
    valueSelect.appendChild(option);
  });
}

const setElementTextById = (id, value) => {
  const el = document.getElementById(id);
  if (el) el.textContent = value ?? 0;
}

const updateNationalOperationalKpis = (kpis) => {
  if (!kpis) return;
  const eas = kpis.eas || {};
  const households = kpis.households || {};
  const people = kpis.people || {};

  setElementTextById("nov-kpi-today-eas", eas.today);
  setElementTextById("nov-kpi-week-eas", eas.week);
  setElementTextById("nov-kpi-30d-eas", eas.last_30_days);
  setElementTextById("nov-kpi-total-eas", eas.total);

  setElementTextById("nov-kpi-today-households", households.today);
  setElementTextById("nov-kpi-week-households", households.week);
  setElementTextById("nov-kpi-30d-households", households.last_30_days);
  setElementTextById("nov-kpi-total-households", households.total);

  setElementTextById("nov-kpi-today-people", people.today);
  setElementTextById("nov-kpi-week-people", people.week);
  setElementTextById("nov-kpi-30d-people", people.last_30_days);
  setElementTextById("nov-kpi-total-people", people.total);
}

let _novKpiInflight = null;

const requestNationalOperationalFilterData = (options = {}) => {
  const started = perfNow();
  const shouldInvalidate = Boolean(options.invalidateScope);
  if (shouldInvalidate) {
    invalidateScope("overview-data");
  }

  const root = document.getElementById("national-operational-view");
  if (!root) return;

  const requestId = ++novFilterRequestId;
  const selectedPreset = selectedNovPreset();
  const locationLevel = (document.getElementById("novLocationLevel")?.value || "national").toLowerCase();
  const locationValue = (document.getElementById("novLocationValue")?.value || "").trim();
  const startDatetime = document.getElementById("novStartDatetime")?.value || "";
  const endDatetime = document.getElementById("novEndDatetime")?.value || "";

  const kpiEndpoint = root.dataset.filterUrl || "";
  if (kpiEndpoint) {
    if (_novKpiInflight) {
      _novKpiInflight.abort();
    }
    const controller = new AbortController();
    _novKpiInflight = controller;
    const params = new URLSearchParams({
      kpis_only: "1",
      preset: selectedPreset,
      start: startDatetime,
      end: endDatetime,
      location_level: locationLevel || "national",
      location_value: locationValue,
    });
    fetch(`${kpiEndpoint}?${params.toString()}`, {
      method: "GET",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
      signal: controller.signal,
    })
      .then((response) => {
        _novKpiInflight = null;
        if (!response.ok) throw new Error(`Failed to load overview filter data: ${response.status}`);
        return response.json();
      })
      .then((jsonResponse) => {
        if (requestId !== novFilterRequestId) return;
        updateNationalOperationalKpis(jsonResponse.kpis || {});
      })
      .catch((error) => {
        if (error.name !== "AbortError") {
          console.error("[baseline-census] failed to fetch overview KPIs", error);
        }
      });
  }

  const mapPanel = document.getElementById("homeOverviewMapPanel");
  const createMapFactory =
    window.createHomeHierarchicalDashboardMap || window.createHierarchicalDashboardMap;
  if (!mapPanel || typeof createMapFactory !== "function") return;

  if (!homeOverviewMapController) {
    const endpoint = mapPanel.dataset.mapEndpoint || "";
    if (!endpoint) return;
    const userCanViewPII = (mapPanel.dataset.userPii || "").toLowerCase() === "true";
    homeOverviewMapController = createMapFactory({
      containerId: "homeOverviewMapContainer",
      legendId: "homeOverviewMapLegend",
      breadcrumbId: "homeOverviewMapBreadcrumb",
      emptyStateId: "homeOverviewMapEmpty",
      endpoint,
      buildParams: (filters = {}) => {
        const params = new URLSearchParams();
        if (filters.time_preset) params.set("time_preset", filters.time_preset);
        if (filters.start_datetime) params.set("start_datetime", filters.start_datetime);
        if (filters.end_datetime) params.set("end_datetime", filters.end_datetime);
        if (filters.map_view) params.set("map_view", filters.map_view);
        if (filters.geography_level && filters.geography_value) {
          params.set("geography_level", filters.geography_level);
          params.set("geography_value", filters.geography_value);
        }
        return params;
      },
      initialView: "Province",
      fitToDataBounds: true,
      styleVariant: "va",
      noDataMessage: "No geographic data available for the selected filters.",
      userCanViewPII,
    });
  }

  const mapViewByLocationLevel = {
    province: "Province",
    district: "District",
    constituency: "Constituency",
    ward: "Ward",
  };

  const filters = {
    time_preset: selectedPreset === "30"
      ? "last_30_days"
      : selectedPreset === "7"
        ? "last_7_days"
        : selectedPreset === "24"
          ? "last_24_hours"
          : "all_time",
    start_datetime: startDatetime,
    end_datetime: endDatetime,
    map_view: mapViewByLocationLevel[locationLevel] || "Province",
  };

  if (locationLevel !== "national" && locationValue) {
    filters.geography_level = locationLevel;
    filters.geography_value = locationValue;
  }

  if (selectedPreset === "all" && (filters.start_datetime || filters.end_datetime)) {
    filters.time_preset = "custom";
  }

  homeOverviewMapController
    .refresh(filters)
    .then(() => perfLog("render.overview.map", started, { mapView: filters.map_view }))
    .catch((error) => console.error("[baseline-census] failed to refresh overview map", error));
}

const initNationalOperationalFilters = () => {
  const root = document.getElementById("national-operational-view");
  if (!root || novFiltersBound) return;
  novFiltersBound = true;

  ["novTimeAll", "novTime30", "novTime7", "novTime24"].forEach((id) => {
    const radio = document.getElementById(id);
    if (!radio) return;
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      const preset = selectedNovPreset();
      applyPresetToDatetimeInputs(preset);
      requestNationalOperationalFilterData({ invalidateScope: true });
    });
  });

  const startInput = document.getElementById("novStartDatetime");
  const endInput = document.getElementById("novEndDatetime");
  const locationLevel = document.getElementById("novLocationLevel");
  const locationValue = document.getElementById("novLocationValue");

  updateNovLocationValueOptions();

  if (locationLevel) {
    locationLevel.addEventListener("change", () => {
      updateNovLocationValueOptions();
      requestNationalOperationalFilterData({ invalidateScope: true });
    });
  }

  if (locationValue) {
    locationValue.addEventListener("change", () => {
      requestNationalOperationalFilterData({ invalidateScope: true });
    });
  }

  [startInput, endInput].forEach((input) => {
    if (!input) return;
    input.addEventListener("change", () => {
      clearPresetSelection();
      requestNationalOperationalFilterData({ invalidateScope: true });
    });
  });
}

const setSingleMetricTrendsTableData = (prefix, trendTable) => {
  const row = (trendTable && trendTable.recorded) ? trendTable.recorded : {};
  const values = {
    "24-hours": row["24"] || 0,
    "week": row["1 week"] || 0,
    "month": row["1 month"] || 0,
    "overall": row["Overall"] || 0,
  };

  Object.entries(values).forEach(([suffix, value]) => {
    const el = document.getElementById(`${prefix}-past-${suffix}`);
    if (el) el.innerHTML = value;
  });
}

const setSingleMetricTrendChart = (canvasId, trendGraphs) => {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !trendGraphs || !trendGraphs.recorded) return;
  const x = trendGraphs.recorded.x || [];
  const y = ensureCumulativeNonDecreasing(trendGraphs.recorded.y || []);
  setSingleTrendChart(canvas, x, y);
}

const setModelTrendVisualizations = (modelTrends) => {
  if (!modelTrends) return;

  const households = modelTrends.households || {};
  setSingleMetricTrendsTableData("households", households.table);
  setSingleMetricTrendChart("householdsChart", households.graphs);

  const pregnancies = modelTrends.pregnancies || {};
  setSingleMetricTrendsTableData("pregnancies", pregnancies.table);
  setSingleMetricTrendChart("pregnanciesChart", pregnancies.graphs);

  const pregnancyOutcomes = modelTrends.pregnancy_outcomes || {};
  setSingleMetricTrendsTableData("pregnancy-outcomes", pregnancyOutcomes.table);
  setSingleMetricTrendChart("pregnancyOutcomesChart", pregnancyOutcomes.graphs);

  const deaths = modelTrends.deaths || {};
  setSingleMetricTrendsTableData("deaths", deaths.table);
  setSingleMetricTrendChart("deathsChart", deaths.graphs);
}

const applyTrendsPayload = (jsonResponse) => {
  const started = perfNow();
  if (!jsonResponse) return;
  setModelTrendVisualizations(jsonResponse.modelTrends || {});
  perfLog("render.trends.components", started);
}

const fetchTrendsPayload = (section = "model") => {
  if (homeDataCacheBySection[section]) return Promise.resolve(homeDataCacheBySection[section]);
  if (homeDataPromiseBySection[section]) return homeDataPromiseBySection[section];

  if (hasJqueryAjax()) {
    homeDataPromiseBySection[section] = new Promise((resolve, reject) => {
      $.ajax({
        url: getTrendsEndpoint(section),
        type: "GET",
        dataType: "json",
        success: (jsonResponse) => {
          homeDataCacheBySection[section] = jsonResponse;
          resolve(jsonResponse);
        },
        error: () => {
          homeDataPromiseBySection[section] = null;
          reject(new Error("Failed to fetch chart data from trends endpoint"));
        },
      });
    });
    return homeDataPromiseBySection[section];
  }

  homeDataPromiseBySection[section] = fetch(getTrendsEndpoint(section), {
    method: "GET",
    headers: { "X-Requested-With": "XMLHttpRequest" },
    credentials: "same-origin",
  })
    .then((response) => {
      if (!response.ok) throw new Error(`Failed to fetch chart data: ${response.status}`);
      return response.json();
    })
    .then((jsonResponse) => {
      homeDataCacheBySection[section] = jsonResponse;
      return jsonResponse;
    })
    .catch((error) => {
      homeDataPromiseBySection[section] = null;
      throw error;
    });
  return homeDataPromiseBySection[section];
}

const resizeChartsForTab = (tab) => {
  const mapByTab = {
    trends: ["householdsChart", "pregnanciesChart", "pregnancyOutcomesChart", "deathsChart"],
  };
  (mapByTab[tab] || []).forEach((id) => {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    const chart = chartInstances.get(canvas);
    if (chart) chart.resize();
  });
  if (tab === "overview" && homeOverviewMapController && typeof homeOverviewMapController.resize === "function") {
    homeOverviewMapController.resize();
  }
}

const initOverviewTab = async () => {
  await Promise.allSettled([
    loadSlotOnce("novKpisComponentSlot"),
  ]);

  if (overviewInitialized) {
    requestNationalOperationalFilterData();
    resizeChartsForTab("overview");
    return;
  }
  overviewInitialized = true;
  initNationalOperationalFilters();
  requestNationalOperationalFilterData();
}

const initTrendsTab = async () => {
  await loadSlotOnce("homeTrendsComponentSlot");

  if (trendsInitialized) {
    resizeChartsForTab("trends");
    return;
  }
  trendsInitialized = true;
  try {
    const payload = await fetchTrendsPayload("model");
    applyTrendsPayload(payload);
    resizeChartsForTab("trends");
  } catch (error) {
    console.error(error);
  }
}

const initCsaSupervisionTab = async () => {
  const filtersSlot = document.getElementById("regionalFiltersComponent");
  const csaSlot     = document.getElementById("regionalCsaComponent");
  if (filtersSlot?.dataset?.loaded === "1" && csaSlot?.dataset?.loaded === "1") return;
  await Promise.allSettled([
    loadSlotOnce("regionalFiltersComponent"),
    loadSlotOnce("regionalCsaComponent"),
  ]);
}

const initBaselineCensusTab = async (tab) => {
  const normalizedTab = tab === "operations_supervision" ? "csa_supervision" : tab;
  const started = perfNow();
  if (normalizedTab === "overview") {
    await initOverviewTab();
    perfLog("tab.init.overview", started);
    return;
  }
  if (normalizedTab === "trends") {
    await initTrendsTab();
    perfLog("tab.init.trends", started);
    return;
  }
  if (normalizedTab === "csa_supervision") {
    await initCsaSupervisionTab();
    perfLog("tab.init.csa_supervision", started);
  }
}

const initialStarted = perfNow();

document.querySelectorAll(`${SHELL_SELECTOR} [data-tab]`).forEach((tabLink) => {
  tabLink.addEventListener("click", () => {
    console.log("[perf][baseline-census] ui.tab_click", { tab: tabLink.dataset.tab });
  });
});

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-action='refresh-component']");
  if (!trigger) return;
  const targetSelector = trigger.dataset.target || "";
  if (targetSelector === "#homeTrendsComponentSlot") {
    clearTrendsCache();
    invalidateScope("trends-data");
  }
  if (targetSelector === "#novKpisComponentSlot") {
    invalidateScope("overview-data");
  }
});

document.addEventListener("dashboard:component-loaded", (event) => {
  const slot = event.target;
  if (!slot || !slot.id) return;

  if (slot.id === "homeTrendsComponentSlot" && trendsInitialized) {
    fetchTrendsPayload("model")
      .then((payload) => {
        applyTrendsPayload(payload);
        resizeChartsForTab("trends");
      })
      .catch((error) => console.error(error));
    return;
  }

  if (slot.id === "novKpisComponentSlot" && overviewInitialized) {
    requestNationalOperationalFilterData();
  }
});

document.addEventListener("dashboard:refresh-tab", (event) => {
  const detail = event?.detail || {};
  if (detail.shell !== "baseline-census") return;
  const tab = detail.tab || "";
  const started = perfNow();
  initBaselineCensusTab(tab)
    .then(() => {
      const durationMs = perfNow() - started;
      if (!firstRenderMeasured) {
        firstRenderMeasured = true;
        perfLog("initial.page_bootstrap", initialStarted, { activeTab: tab });
        console.log("[perf][acceptance] baseline_census.first_render_ms", {
          durationMs: Number((perfNow() - initialStarted).toFixed(2)),
        });
      }
      if (tabWarmCache.has(tab)) {
        const nearInstantThresholdMs = 220;
        const pass = durationMs <= nearInstantThresholdMs;
        console.log("[perf][acceptance] tab_switch_cached", {
          tab,
          durationMs: Number(durationMs.toFixed(2)),
          thresholdMs: nearInstantThresholdMs,
          pass,
        });
      } else {
        tabWarmCache.add(tab);
      }
    })
    .catch((error) => console.error(error));
});
