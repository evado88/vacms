GRAPH_OPTIONS = {
  responsive: true,
  scales: { y: { beginAtZero: true } },
  plugins: { legend: { display: false } }
};

BORDER_COLOR = "#037BFE"
const chartInstances = new WeakMap();
let novFiltersBound = false;
let overviewInitialized = false;
let vaStatsInitialized = false;
let trendsInitialized = false;
let homeDataPromise = null;
let homeDataCache = null;
let homeDataPromiseBySection = {};
let homeDataCacheBySection = {};
let firstRenderMeasured = false;
const tabWarmCache = new Set();
let homeOverviewMapController = null;
let novFilterRequestId = 0;

const perfNow = () => (
  window.performance && typeof window.performance.now === "function"
    ? window.performance.now()
    : Date.now()
);
const perfLog = (name, startMs, meta) => {
  const durationMs = perfNow() - startMs;
  if (meta) {
    console.log(`[perf][home] ${name} ${durationMs.toFixed(2)}ms`, meta);
  } else {
    console.log(`[perf][home] ${name} ${durationMs.toFixed(2)}ms`);
  }
};

const getDashboardLoader = () => window.DashboardLoader || null;
const hasJqueryAjax = () => typeof window !== "undefined" && window.$ && typeof window.$.ajax === "function";
const getHomeTrendsEndpoint = (section = "all") => {
  const shell = document.querySelector(".dashboard-shell[data-shell='home']");
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
    throw new Error(`Failed to load home slot from ${endpoint}`);
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
    console.error(`[home] failed to load slot: ${slotId}`, error);
  }
}

const refreshSlot = async (slotId) => {
  const slot = document.getElementById(slotId);
  const loader = getDashboardLoader();
  if (!slot) return;
  try {
    if (loader && typeof loader.refreshComponent === "function") {
      await loader.refreshComponent(slot);
      return;
    }
    await fetchSlotHtmlFallback(slot);
  } catch (error) {
    console.error(`[home] failed to refresh slot: ${slotId}`, error);
  }
}

const invalidateScope = (scopeKey) => {
  const loader = getDashboardLoader();
  if (!loader || typeof loader.invalidateComponentsByScope !== "function") return;
  loader.invalidateComponentsByScope(scopeKey);
}

const clearHomeTrendsCache = () => {
  homeDataCache = null;
  homeDataPromise = null;
  homeDataPromiseBySection = {};
  homeDataCacheBySection = {};
}

const setHtmlIfPresent = (id, value) => {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = value == null ? "" : value;
}

const setVATrendsTableData = (vaTableData) => {
  const started = perfNow();
  const collected = vaTableData?.collected || {};
  const coded = vaTableData?.coded || {};
  const uncoded = vaTableData?.uncoded || {};

  setHtmlIfPresent('interviewed-past-24-hours', collected["24"] || 0);
  setHtmlIfPresent('interviewed-past-week', collected["1 week"] || 0);
  setHtmlIfPresent('interviewed-past-month', collected["1 month"] || 0);
  setHtmlIfPresent('interviewed-overall', collected["Overall"] || 0);

  setHtmlIfPresent('coded-past-24-hours', coded["24"] || 0);
  setHtmlIfPresent('coded-past-week', coded["1 week"] || 0);
  setHtmlIfPresent('coded-past-month', coded["1 month"] || 0);
  setHtmlIfPresent('coded-overall', coded["Overall"] || 0);

  setHtmlIfPresent('uncoded-past-24-hours', uncoded["24"] || 0);
  setHtmlIfPresent('uncoded-past-week', uncoded["1 week"] || 0);
  setHtmlIfPresent('uncoded-past-month', uncoded["1 month"] || 0);
  setHtmlIfPresent('uncoded-overall', uncoded["Overall"] || 0);
  perfLog("render.va_statistics.table", started);
}

const setVARow = (root, row, isFieldWorker) => {
  if(isFieldWorker) {
    root.insertAdjacentHTML('beforebegin',
`<tr>
        <td>${row.id}</td>
        <td>${row.interviewed}</td>
        <td>${row.facility}</td>
        <td>${row.deceased}</td>
        <td>${row.dod}</td>
        <td>${row.cause}</td>
        <td>${row.warnings}</td>
        <td>${row.errors}</td>
        <td><a class="btn btn-primary" href="va_data_management/show/${row.id}">View</a></td>
      </tr>`
    )
  }
  else {
    root.insertAdjacentHTML('beforebegin',
`<tr>
        <td>${row.id}</td>
        <td>${row.interviewed}</td>
        <td>${row.interviewer}</td>
        <td>${row.facility}</td>
        <td>${row.deceased}</td>
        <td>${row.dod}</td>
        <td>${row.cause}</td>
        <td>${row.warnings}</td>
        <td>${row.errors}</td>
        <td><a class="btn btn-primary" href="va_data_management/show/${row.id}">View</a></td>
      </tr>`
    )
  }
}

const setCodingIssuesTableData = (codingIssuesData, isFieldWorker) => {
  const root = document.getElementById('coding-issues-root');
  if (!root) return;
  root.parentElement.querySelectorAll("tr:not(#coding-issues-root)").forEach((row) => row.remove());
  codingIssuesData.forEach(row => setVARow(root, row, isFieldWorker));
}

const setIndeterminateCODTableData = (codingIssuesData, isFieldWorker) => {
  const root = document.getElementById('indeterminate-cod-root');
  if (!root) return;
  root.parentElement.querySelectorAll("tr:not(#indeterminate-cod-root)").forEach((row) => row.remove());
  codingIssuesData.forEach(row => setVARow(root, row, isFieldWorker));
}

const setVAChart = (x, y, canvas) => {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const existingChart = chartInstances.get(canvas);
  if (existingChart) {
    existingChart.destroy();
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

  // Backward-compatibility guard: if stale monthly values are returned,
  // convert them to cumulative for display.
  let runningTotal = 0;
  return numericValues.map((value) => {
    runningTotal += value;
    return runningTotal;
  });
}

const setVACharts = (graphData) => {
  const interviewedCanvas = document.getElementById("interviewedChart");
  const codedCanvas = document.getElementById("codedChart");
  const notYetCodedCanvas = document.getElementById("notYetCodedChart");
  if (!interviewedCanvas || !codedCanvas || !notYetCodedCanvas) return;
  const collected = graphData?.collected || { x: [], y: [] };
  const coded = graphData?.coded || { x: [], y: [] };
  const uncoded = graphData?.uncoded || { x: [], y: [] };
  setVAChart(collected.x || [], ensureCumulativeNonDecreasing(collected.y || []), interviewedCanvas);
  setVAChart(coded.x || [], ensureCumulativeNonDecreasing(coded.y || []), codedCanvas);
  setVAChart(uncoded.x || [], ensureCumulativeNonDecreasing(uncoded.y || []), notYetCodedCanvas);
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
  const pregnancies = kpis.pregnancies || {};
  const pregOutcomes = kpis.preg_outcomes || {};
  const deaths = kpis.deaths || {};
  const vas = kpis.vas || {};

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

  setElementTextById("nov-kpi-today-pregnancies", pregnancies.today);
  setElementTextById("nov-kpi-week-pregnancies", pregnancies.week);
  setElementTextById("nov-kpi-30d-pregnancies", pregnancies.last_30_days);
  setElementTextById("nov-kpi-total-pregnancies", pregnancies.total);

  setElementTextById("nov-kpi-today-preg-outcomes", pregOutcomes.today);
  setElementTextById("nov-kpi-week-preg-outcomes", pregOutcomes.week);
  setElementTextById("nov-kpi-30d-preg-outcomes", pregOutcomes.last_30_days);
  setElementTextById("nov-kpi-total-preg-outcomes", pregOutcomes.total);

  setElementTextById("nov-kpi-today-deaths", deaths.today);
  setElementTextById("nov-kpi-week-deaths", deaths.week);
  setElementTextById("nov-kpi-30d-deaths", deaths.last_30_days);
  setElementTextById("nov-kpi-total-deaths", deaths.total);

  setElementTextById("nov-kpi-today-vas", vas.today);
  setElementTextById("nov-kpi-week-vas", vas.week);
  setElementTextById("nov-kpi-30d-vas", vas.last_30_days);
  setElementTextById("nov-kpi-total-vas", vas.total);
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
    // Abort any in-flight KPI request before starting a new one so rapid
    // filter changes or tab switches don't queue up stale server calls.
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
          console.error("[home] failed to fetch National Operational KPIs", error);
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
      noDataMessage: "No geographic pregnancy data available for the selected filters.",
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
    .catch((error) => console.error("[home] failed to refresh overview map", error));
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
  setVAChart(x, y, canvas);
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

const applyVAStatisticsPayload = (jsonResponse) => {
  const started = perfNow();
  if (!jsonResponse) return;

  setVATrendsTableData(jsonResponse.vaTable || {});
  setVACharts(jsonResponse.graphs || {});

  if ((jsonResponse.issueList || []).length > 0) {
    setCodingIssuesTableData(jsonResponse.issueList, jsonResponse.isFieldWorker);
    $('#coding-issues').removeClass('hidden');
    $('#no-coding-issues').addClass('hidden');

    if (jsonResponse.additionalIssues > 0) {
      setHtmlIfPresent('additional-issues-count', jsonResponse.additionalIssues);
      $('#additional-issues-msg').removeClass('hidden');
    } else {
      $('#additional-issues-msg').addClass('hidden');
    }
  } else {
    $('#coding-issues').addClass('hidden');
    $('#additional-issues-msg').addClass('hidden');
    $('#no-coding-issues').removeClass('hidden');
  }

  if ((jsonResponse.indeterminateCodList || []).length > 0) {
    setIndeterminateCODTableData(jsonResponse.indeterminateCodList, jsonResponse.isFieldWorker);
    $('#indeterminate-cod').removeClass('hidden');
    $('#no-indeterminate-cod').addClass('hidden');

    if (jsonResponse.additionalIndeterminateCods > 0) {
      setHtmlIfPresent('additional-indeterminate-cods-count', jsonResponse.additionalIndeterminateCods);
      $('#additional-indeterminate-cods-msg').removeClass('hidden');
    } else {
      $('#additional-indeterminate-cods-msg').addClass('hidden');
    }
  } else {
    $('#indeterminate-cod').addClass('hidden');
    $('#additional-indeterminate-cods-msg').addClass('hidden');
    $('#no-indeterminate-cod').removeClass('hidden');
  }
  perfLog("render.va_statistics.components", started);
}

const applyTrendsPayload = (jsonResponse) => {
  const started = perfNow();
  if (!jsonResponse) return;
  setModelTrendVisualizations(jsonResponse.modelTrends || {});
  perfLog("render.trends.components", started);
}

const fetchHomeTrendsPayload = (section = "all") => {
  if (homeDataCacheBySection[section]) return Promise.resolve(homeDataCacheBySection[section]);
  if (homeDataPromiseBySection[section]) return homeDataPromiseBySection[section];

  // Backward compatibility for any all-sections callers.
  if (section === "all" && homeDataCache) return Promise.resolve(homeDataCache);
  if (section === "all" && homeDataPromise) return homeDataPromise;

  if (hasJqueryAjax()) {
    homeDataPromiseBySection[section] = new Promise((resolve, reject) => {
      $.ajax({
        url: getHomeTrendsEndpoint(section),
        type: "GET",
        dataType: "json",
        success: (jsonResponse) => {
          homeDataCacheBySection[section] = jsonResponse;
          if (section === "all") homeDataCache = jsonResponse;
          resolve(jsonResponse);
        },
        error: () => {
          homeDataPromiseBySection[section] = null;
          if (section === "all") homeDataPromise = null;
          reject(new Error("Failed to fetch chart data from /trends/"));
        },
      });
    });
    if (section === "all") homeDataPromise = homeDataPromiseBySection[section];
    return homeDataPromiseBySection[section];
  }

  homeDataPromiseBySection[section] = fetch(getHomeTrendsEndpoint(section), {
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
      if (section === "all") homeDataCache = jsonResponse;
      return jsonResponse;
    })
    .catch((error) => {
      homeDataPromiseBySection[section] = null;
      if (section === "all") homeDataPromise = null;
      throw error;
    });
  if (section === "all") homeDataPromise = homeDataPromiseBySection[section];
  return homeDataPromiseBySection[section];
}

const resizeChartsForTab = (tab) => {
  const mapByTab = {
    trends: ["householdsChart", "pregnanciesChart", "pregnancyOutcomesChart", "deathsChart"],
    va_statistics: ["interviewedChart", "codedChart", "notYetCodedChart"],
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

const initVAStatisticsTab = async () => {
  await loadSlotOnce("homeVAStatisticsComponentSlot");

  if (vaStatsInitialized) {
    resizeChartsForTab("va_statistics");
    return;
  }
  vaStatsInitialized = true;
  try {
    const payload = await fetchHomeTrendsPayload("va");
    applyVAStatisticsPayload(payload);
    resizeChartsForTab("va_statistics");
  } catch (error) {
    console.error(error);
  }
}

const initTrendsTab = async () => {
  await loadSlotOnce("homeTrendsComponentSlot");

  if (trendsInitialized) {
    resizeChartsForTab("trends");
    return;
  }
  trendsInitialized = true;
  try {
    const payload = await fetchHomeTrendsPayload("model");
    applyTrendsPayload(payload);
    resizeChartsForTab("trends");
  } catch (error) {
    console.error(error);
  }
}

const initCsaSupervisionTab = async () => {
  // Fast path: both slots already populated — tab switch is CSS show/hide only.
  const filtersSlot = document.getElementById("regionalFiltersComponent");
  const csaSlot     = document.getElementById("regionalCsaComponent");
  if (filtersSlot?.dataset?.loaded === "1" && csaSlot?.dataset?.loaded === "1") return;
  await Promise.allSettled([
    loadSlotOnce("regionalFiltersComponent"),
    loadSlotOnce("regionalCsaComponent"),
  ]);
}

const initMsoSupervisionTab = async () => {
  // Fast path: both slots already populated — tab switch is CSS show/hide only.
  const msoFiltersSlot = document.getElementById("regionalMsoFiltersComponent");
  const msoSlot        = document.getElementById("regionalMsoComponent");
  if (msoFiltersSlot?.dataset?.loaded === "1" && msoSlot?.dataset?.loaded === "1") return;
  await loadSlotOnce("regionalMsoFiltersComponent");
  await loadSlotOnce("regionalMsoComponent");
}

const initHomeTab = async (tab) => {
  const normalizedTab = tab === "operations_supervision" ? "csa_supervision" : tab;
  const started = perfNow();
  if (normalizedTab === "overview") {
    await initOverviewTab();
    perfLog("tab.init.overview", started);
    return;
  }
  if (normalizedTab === "va_statistics") {
    await initVAStatisticsTab();
    perfLog("tab.init.va_statistics", started);
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
    return;
  }
  if (normalizedTab === "mso_supervision") {
    await initMsoSupervisionTab();
    perfLog("tab.init.mso_supervision", started);
  }
}

const detectActiveHomeTab = () =>
  document.querySelector(".dashboard-shell[data-shell='home'] .dashboard-tab-panel.show.active")?.dataset?.tabPanel
  || "overview";

// dashboard_tabs.js dispatches "dashboard:refresh-tab" for the initial active tab,
// so we rely on that event handler below instead of calling initHomeTab here.
// This prevents the double-init that caused duplicate map-data/filter-data fetches.
const initialStarted = perfNow();

document.querySelectorAll(".dashboard-shell[data-shell='home'] [data-tab]").forEach((tabLink) => {
  tabLink.addEventListener("click", () => {
    console.log("[perf][home] ui.tab_click", { tab: tabLink.dataset.tab });
  });
});

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-action='refresh-component']");
  if (!trigger) return;
  const targetSelector = trigger.dataset.target || "";
  if (targetSelector === "#homeTrendsComponentSlot" || targetSelector === "#homeVAStatisticsComponentSlot") {
    clearHomeTrendsCache();
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
    fetchHomeTrendsPayload("model")
      .then((payload) => {
        applyTrendsPayload(payload);
        resizeChartsForTab("trends");
      })
      .catch((error) => console.error(error));
    return;
  }

  if (slot.id === "homeVAStatisticsComponentSlot" && vaStatsInitialized) {
    fetchHomeTrendsPayload("va")
      .then((payload) => {
        applyVAStatisticsPayload(payload);
        resizeChartsForTab("va_statistics");
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
  if (detail.shell !== "home") return;
  const tab = detail.tab || "";
  const started = perfNow();
  initHomeTab(tab)
    .then(() => {
      const durationMs = perfNow() - started;
      if (!firstRenderMeasured) {
        firstRenderMeasured = true;
        perfLog("initial.page_bootstrap", initialStarted, { activeTab: tab });
        console.log("[perf][acceptance] home.first_render_ms", {
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
