const chartInstances = new WeakMap();
let novFiltersBound = false;
let overviewInitialized = false;
let firstRenderMeasured = false;
const tabWarmCache = new Set();
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

const destroyChartForCanvas = (canvas) => {
  const existingChart = chartInstances.get(canvas);
  if (!existingChart) return;
  existingChart.destroy();
  chartInstances.delete(canvas);
}

const formatCountValue = (value) => {
  if (value === null || value === undefined) return "";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "";
  return numeric.toLocaleString();
}

const formatCoverageValue = (value) => {
  if (value === null || value === undefined) return "";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "";
  return `${numeric}%`;
}

const parseNumericOrNull = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

const buildEnumerationCoverageSeries = (kpis = {}) => {
  const peopleTotal = parseNumericOrNull(kpis.people?.total);
  const householdsTotal = parseNumericOrNull(kpis.households?.total);
  const pregnanciesTotal = parseNumericOrNull(kpis.pregnancies?.total);
  const pregOutcomesTotal = parseNumericOrNull(kpis.preg_outcomes?.total);
  const deathsTotal = parseNumericOrNull(kpis.deaths?.total);

  const labels = [
    "Population",
    "Household",
    "Pregnancies",
    "Pregnancy Outcomes",
    "Deaths",
  ];

  const estimateData = [10000, 5000, null, null, null];
  const enumeratedData = [
    peopleTotal ?? 9000,
    householdsTotal ?? 3500,
    pregnanciesTotal ?? 0,
    pregOutcomesTotal ?? 0,
    deathsTotal ?? 0,
  ];
  const coverageData = [90, 70, null, null, null];

  return { labels, estimateData, enumeratedData, coverageData };
}

const enumerationCoverageLabelsPlugin = {
  id: "enumerationCoverageLabels",
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    ctx.save();
    ctx.font = "600 11px sans-serif";
    ctx.textAlign = "center";

    chart.data.datasets.forEach((dataset, datasetIndex) => {
      const meta = chart.getDatasetMeta(datasetIndex);
      if (meta.hidden) return;

      meta.data.forEach((element, index) => {
        const value = dataset.data[index];
        if (value === null || value === undefined) return;

        const isCoverage = dataset.yAxisID === "yCoverage";
        const label = isCoverage ? formatCoverageValue(value) : formatCountValue(value);
        if (!label) return;

        ctx.fillStyle = isCoverage ? "#166534" : "#1f2937";
        ctx.textBaseline = "bottom";
        ctx.fillText(label, element.x, element.y - 6);
      });
    });

    ctx.restore();
  },
}

const renderEnumerationCoverageChart = (kpis = {}) => {
  const canvas = document.getElementById("enumerationCoverageChart");
  if (!canvas || typeof Chart === "undefined") return;

  destroyChartForCanvas(canvas);

  const {
    labels,
    estimateData,
    enumeratedData,
    coverageData,
  } = buildEnumerationCoverageSeries(kpis);

  const chart = new Chart(canvas.getContext("2d"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          type: "bar",
          label: "Estimate",
          data: estimateData,
          backgroundColor: "#2563eb",
          borderRadius: 4,
          yAxisID: "y",
        },
        {
          type: "bar",
          label: "Enumerated",
          data: enumeratedData,
          backgroundColor: "#dc2626",
          borderRadius: 4,
          yAxisID: "y",
        },
        {
          type: "line",
          label: "Coverage",
          data: coverageData,
          borderColor: "#16a34a",
          backgroundColor: "#16a34a",
          borderWidth: 2,
          pointBackgroundColor: "#16a34a",
          pointBorderColor: "#ffffff",
          pointBorderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 5,
          spanGaps: false,
          tension: 0.25,
          yAxisID: "yCoverage",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        intersect: false,
        mode: "index",
      },
      layout: {
        padding: {
          top: 22,
        },
      },
      plugins: {
        legend: {
          display: true,
          position: "top",
          labels: {
            boxWidth: 12,
            boxHeight: 12,
          },
        },
        tooltip: {
          callbacks: {
            label(context) {
              const value = context.raw;
              if (value === null || value === undefined) return "";
              const label = context.dataset.label || "";
              const formatted = context.dataset.yAxisID === "yCoverage"
                ? formatCoverageValue(value)
                : formatCountValue(value);
              return `${label}: ${formatted}`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: {
            display: false,
          },
        },
        y: {
          beginAtZero: true,
          position: "left",
          title: {
            display: true,
            text: "Counts",
          },
          ticks: {
            callback: (value) => formatCountValue(value),
          },
        },
        yCoverage: {
          beginAtZero: true,
          max: 100,
          position: "right",
          title: {
            display: true,
            text: "Coverage",
          },
          grid: {
            drawOnChartArea: false,
          },
          ticks: {
            callback: (value) => formatCoverageValue(value),
          },
        },
      },
    },
    plugins: [enumerationCoverageLabelsPlugin],
  });
  chartInstances.set(canvas, chart);
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

  renderEnumerationCoverageChart(kpis);
}

let _novKpiInflight = null;

// ---------------------------------------------------------------------------
// Province census table (Overview tab)
// ---------------------------------------------------------------------------
let bcProvinceTableInitialized = false;
let bcSearchTimer = null;
const bcSortDefaults = {
  bc_sort: "province",
  bc_dir: "asc",
  bc_province_search: "",
  bc_district_search: "",
  bc_ward_search: "",
  bc_ea_search: "",
  bc_page: "1",
};
let bcSortState = Object.assign({}, bcSortDefaults);

let bcTableFilterState = {
  location_level: 'national',
  location_value: '',
  start: '',
  end: '',
  preset: '',
  current: true,
};

const BC_SEARCH_IDS = ["bcProvinceSearch", "bcDistrictSearch", "bcWardSearch", "bcEaSearch"];
const BC_STATE_KEY_MAP = {
  bcProvinceSearch: "bc_province_search",
  bcDistrictSearch: "bc_district_search",
  bcWardSearch: "bc_ward_search",
  bcEaSearch: "bc_ea_search",
};

const getBcProvinceTableUrl = () => {
  const slot = document.getElementById("baselineProvinceTableSlot");
  return slot ? (slot.dataset.endpoint || "") : "";
};

const refreshBcProvinceTable = () => {
  const slot = document.getElementById("baselineProvinceTableSlot");
  const url = getBcProvinceTableUrl();
  if (!slot || !url) return;

  const activeBcSearchId = BC_SEARCH_IDS.find((id) => document.activeElement?.id === id) || null;

  const params = new URLSearchParams();
  Object.entries(bcSortState).forEach(([key, val]) => {
    if (val) params.set(key, val);
  });

  // Merge Table tab location/time filter params if provided
  if (bcTableFilterState.location_level && bcTableFilterState.location_value) {
    params.set('location_level', bcTableFilterState.location_level);
    params.set('location_value', bcTableFilterState.location_value);
  }
  if (bcTableFilterState.start) params.set('start', bcTableFilterState.start);
  if (bcTableFilterState.end) params.set('end', bcTableFilterState.end);
  if (bcTableFilterState.preset) params.set('preset', bcTableFilterState.preset);
  params.set('bc_current', bcTableFilterState.current ? '1' : '0');

  fetch(`${url}?${params.toString()}`, {
    method: "GET",
    headers: { "X-Requested-With": "XMLHttpRequest" },
    credentials: "same-origin",
  })
    .then((res) => {
      if (!res.ok) throw new Error(`Province table fetch failed: ${res.status}`);
      return res.text();
    })
    .then((html) => {
      slot.innerHTML = html;
      slot.dataset.loaded = "1";
      if (activeBcSearchId) {
        const input = document.getElementById(activeBcSearchId);
        if (input) {
          input.focus();
          const len = input.value.length;
          input.setSelectionRange(len, len);
        }
      }
    })
    .catch((err) => console.error("[baseline-census] province table refresh failed", err));
};

const wireBcProvinceTableEvents = () => {
  document.addEventListener("input", (event) => {
    const target = event.target;
    if (!target || !BC_SEARCH_IDS.includes(target.id)) return;
    const stateKey = BC_STATE_KEY_MAP[target.id];
    if (stateKey) bcSortState[stateKey] = (target.value || "").trim();
    bcSortState.bc_page = "1";
    if (bcSearchTimer) clearTimeout(bcSearchTimer);
    bcSearchTimer = setTimeout(refreshBcProvinceTable, 1000);
  });

  document.addEventListener("click", (event) => {
    const link = event.target.closest(".bc-sort-link, .bc-page-link");
    if (!link) return;
    if (link.classList.contains("disabled") || link.getAttribute("aria-disabled") === "true") return;
    if (!link.closest("#baselineProvinceTableSlot")) return;

    event.preventDefault();
    const href = link.getAttribute("href") || "";
    const params = new URLSearchParams(href.replace("?", ""));

    if (link.classList.contains("bc-sort-link")) {
      bcSortState.bc_sort = params.get("bc_sort") || bcSortState.bc_sort;
      bcSortState.bc_dir = params.get("bc_dir") || bcSortState.bc_dir;
      bcSortState.bc_page = "1";
    } else {
      bcSortState.bc_page = params.get("bc_page") || bcSortState.bc_page;
    }
    refreshBcProvinceTable();
  });
};

const initBcProvinceTable = async () => {
  await loadSlotOnce("baselineProvinceTableSlot");
  if (bcProvinceTableInitialized) return;
  bcProvinceTableInitialized = true;
  wireBcProvinceTableEvents();
};

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

  perfLog("render.overview.kpis", started, {
    locationLevel,
    hasLocationValue: Boolean(locationValue),
    selectedPreset,
  });
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

const updateTableLocationValueOptions = (level) => {
  const valueSelect = document.getElementById("tableLocationValue");
  if (!valueSelect) return;

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

const resetBcSortState = () => {
  bcSortState = Object.assign({}, bcSortDefaults);
}

const initTableFilters = () => {
  const levelSelect = document.getElementById("tableLocationLevel");
  const valueSelect = document.getElementById("tableLocationValue");

  const currentCensusCheckbox = document.getElementById("tableCurrentCensus");
  if (currentCensusCheckbox) {
    bcTableFilterState.current = currentCensusCheckbox.checked;
    currentCensusCheckbox.addEventListener("change", function () {
      bcTableFilterState.current = this.checked;
      resetBcSortState();
      refreshBcProvinceTable();
    });
  }

  if (levelSelect) {
    updateTableLocationValueOptions("national");
    levelSelect.addEventListener("change", function () {
      updateTableLocationValueOptions(this.value);
      bcTableFilterState.location_level = this.value;
      bcTableFilterState.location_value = '';
      resetBcSortState();
      refreshBcProvinceTable();
    });
  }

  if (valueSelect) {
    valueSelect.addEventListener("change", function () {
      bcTableFilterState.location_value = this.value;
      resetBcSortState();
      refreshBcProvinceTable();
    });
  }

  ["tableTimeAll", "tableTime30", "tableTime7", "tableTime24"].forEach(id => {
    const radio = document.getElementById(id);
    if (!radio) return;
    radio.addEventListener("change", function () {
      if (!this.checked) return;
      const presetMap = {
        tableTimeAll: '',
        tableTime30: '30',
        tableTime7: '7',
        tableTime24: '24',
      };
      const preset = presetMap[id] || '';
      bcTableFilterState.preset = preset;

      if (preset) {
        const startInput = document.getElementById("tableStartDatetime");
        const endInput = document.getElementById("tableEndDatetime");
        if (startInput && endInput) {
          const now = new Date();
          const start = new Date(now);
          if (preset === '30') start.setDate(start.getDate() - 30);
          if (preset === '7') start.setDate(start.getDate() - 7);
          if (preset === '24') start.setHours(start.getHours() - 24);
          startInput.value = toDateString(start);
          endInput.value = toDateString(now);
          bcTableFilterState.start = startInput.value;
          bcTableFilterState.end = endInput.value;
        }
      } else {
        bcTableFilterState.start = '';
        bcTableFilterState.end = '';
      }
      resetBcSortState();
      refreshBcProvinceTable();
    });
  });

  const startInput = document.getElementById("tableStartDatetime");
  const endInput = document.getElementById("tableEndDatetime");

  if (startInput) {
    startInput.addEventListener("change", function () {
      bcTableFilterState.start = this.value;
      resetBcSortState();
      refreshBcProvinceTable();
    });
  }

  if (endInput) {
    endInput.addEventListener("change", function () {
      bcTableFilterState.end = this.value;
      resetBcSortState();
      refreshBcProvinceTable();
    });
  }
}

const resizeChartsForTab = (tab) => {
  const mapByTab = {
    overview: ["enumerationCoverageChart"],
  };
  (mapByTab[tab] || []).forEach((id) => {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    const chart = chartInstances.get(canvas);
    if (chart) chart.resize();
  });
}

const initOverviewTab = async () => {
  await loadSlotOnce("novKpisComponentSlot");
  renderEnumerationCoverageChart();

  if (overviewInitialized) {
    requestNationalOperationalFilterData();
    resizeChartsForTab("overview");
    return;
  }
  overviewInitialized = true;
  initNationalOperationalFilters();
  requestNationalOperationalFilterData();
}

let tableTabInitialized = false;

const initTableTab = async () => {
  if (tableTabInitialized) return;
  tableTabInitialized = true;
  await initBcProvinceTable();
  initTableFilters();
}

const initBaselineCensusTab = async (tab) => {
  const normalizedTab = tab === "table" ? "table" : "overview";
  const started = perfNow();
  if (normalizedTab === "overview") {
    await initOverviewTab();
    perfLog("tab.init.overview", started);
    return;
  }
  if (normalizedTab === "table") {
    await initTableTab();
    perfLog("tab.init.table", started);
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
  if (targetSelector === "#novKpisComponentSlot") {
    invalidateScope("overview-data");
  }
});

document.addEventListener("dashboard:component-loaded", (event) => {
  const slot = event.target;
  if (!slot || !slot.id) return;

  if (slot.id === "novKpisComponentSlot" && overviewInitialized) {
    renderEnumerationCoverageChart();
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
