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

  // Population/household estimates come from the SRS EA population target
  // table (summed server-side). Pregnancies/outcomes/deaths have no target.
  const populationTarget = parseNumericOrNull(kpis.targets?.population_target);
  const householdTarget = parseNumericOrNull(kpis.targets?.household_target);

  const labels = [
    "Population",
    "Household",
    "Pregnancies",
    "Pregnancy Outcomes",
    "Deaths",
  ];

  const estimateData = [populationTarget, householdTarget, null, null, null];
  const enumeratedData = [
    peopleTotal ?? 0,
    householdsTotal ?? 0,
    pregnanciesTotal ?? 0,
    pregOutcomesTotal ?? 0,
    deathsTotal ?? 0,
  ];

  // Coverage = enumerated / estimate, as a whole percent — matching how the
  // Census Table derives its coverage columns.
  const coveragePct = (enumerated, estimate) => {
    if (estimate === null || estimate <= 0 || enumerated === null) return null;
    return Math.round((enumerated / estimate) * 100);
  };
  const coverageData = [
    coveragePct(peopleTotal, populationTarget),
    coveragePct(householdsTotal, householdTarget),
    null,
    null,
    null,
  ];

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
  current: false,
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
  const currentCensusCheckbox = document.getElementById("novCurrentCensus");
  const bcCurrent = currentCensusCheckbox ? currentCensusCheckbox.checked : false;

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
      bc_current: bcCurrent ? "1" : "0",
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
  const currentCensusCheckbox = document.getElementById("novCurrentCensus");

  updateNovLocationValueOptions();

  if (currentCensusCheckbox) {
    currentCensusCheckbox.addEventListener("change", () => {
      requestNationalOperationalFilterData({ invalidateScope: true });
    });
  }

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
  if (tab === "chart") {
    // Only the active Census Chart sub-tab canvas is visible and worth sizing.
    const canvas = document.getElementById(
      censusSubtabByKey(bcChartActiveSubtab).canvasId
    );
    const chart = canvas && chartInstances.get(canvas);
    if (chart) chart.resize();
    return;
  }
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

// ---------------------------------------------------------------------------
// Census Chart (drill-down bar + line charts, one focused sub-tab per theme)
// ---------------------------------------------------------------------------
// Population and Households each have an enumeration target, so their sub-tabs
// chart estimate + enumerated bars with a coverage line ("coverage" mode).
// Deaths, Pregnancies and Pregnancy Outcomes have no target, so they share one
// "Vital Events" sub-tab that charts their enumerated counts side by side
// ("counts" mode).
const CENSUS_SUBTABS = [
  {
    key: "population",
    label: "Population",
    canvasId: "censusPopulationChart",
    mode: "coverage",
    indicatorKey: "population",
  },
  {
    key: "household",
    label: "Households",
    canvasId: "censusHouseholdsChart",
    mode: "coverage",
    indicatorKey: "household",
  },
  {
    key: "vital_events",
    label: "Vital Events",
    canvasId: "censusVitalEventsChart",
    mode: "counts",
    series: [
      { key: "death", label: "Deaths", color: "#dc2626" },
      { key: "pregnancy", label: "Pregnancies", color: "#7c3aed" },
      { key: "pregnancy_outcome", label: "Pregnancy Outcomes", color: "#0891b2" },
    ],
  },
];

const censusSubtabByKey = (key) =>
  CENSUS_SUBTABS.find((subtab) => subtab.key === key) || CENSUS_SUBTABS[0];

let bcChartActiveSubtab = "population";

// Drill-down hint copy per level: which geography the bars represent and what
// the next level down is called.
const CENSUS_LEVEL_LABELS = {
  province: { singular: "province", children: "districts" },
  district: { singular: "district", children: "constituencies" },
  constituency: { singular: "constituency", children: "wards" },
  ward: { singular: "ward", children: "EAs" },
  ea: { singular: "EA", children: null },
};

let chartTabInitialized = false;
let chartFiltersBound = false;
let chartFetchRequestId = 0;
let lastChartPayload = null;

let bcChartFilterState = {
  location_level: "national",
  location_value: "",
  start: "",
  end: "",
  preset: "",
  current: false,
};
// Drill-down position: the level currently shown plus the accumulated
// ancestor filters (province/district/constituency/ward) that produced it.
let bcChartNav = { level: "province", parents: {} };

const resetChartNav = () => {
  bcChartNav = { level: "province", parents: {} };
};

const getCensusChartUrl = () => {
  const root = document.getElementById("baseline-chart-view");
  return root ? (root.dataset.endpoint || "") : "";
};

const setCensusChartOverlay = (state) => {
  const overlays = {
    loading: "baselineChartLoading",
    empty: "baselineChartEmpty",
    error: "baselineChartError",
  };
  Object.values(overlays).forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  });
  const activeId = overlays[state];
  if (activeId) {
    const el = document.getElementById(activeId);
    if (el) el.hidden = false;
  }
};

const updateChartLocationValueOptions = (level) => {
  const valueSelect = document.getElementById("chartLocationValue");
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
};

const renderCensusBreadcrumb = (payload) => {
  const nav = document.getElementById("baselineChartBreadcrumb");
  const backBtn = document.getElementById("baselineChartBack");
  if (!nav) return;

  const crumbs = (payload && payload.breadcrumbs) || [];
  nav.innerHTML = "";
  crumbs.forEach((crumb, index) => {
    if (index > 0) {
      const sep = document.createElement("span");
      sep.className = "baseline-chart-breadcrumb-sep";
      sep.textContent = "›";
      nav.appendChild(sep);
    }
    const isLast = index === crumbs.length - 1;
    if (isLast) {
      const current = document.createElement("span");
      current.className = "baseline-chart-breadcrumb-current";
      current.textContent = crumb.label;
      nav.appendChild(current);
    } else {
      const link = document.createElement("button");
      link.type = "button";
      link.className = "baseline-chart-breadcrumb-link";
      link.textContent = crumb.label;
      link.addEventListener("click", () => navigateCensusChartTo(crumb));
      nav.appendChild(link);
    }
  });
  if (backBtn) backBtn.hidden = crumbs.length <= 1;
};

const navigateCensusChartTo = (crumb) => {
  if (!crumb) return;
  bcChartNav = { level: crumb.level, parents: { ...(crumb.parents || {}) } };
  fetchCensusChartData();
};

// Instructional label: tells users they can click bars to drill down, and
// names the level they will drill into next.
const renderCensusChartHint = (payload) => {
  const hint = document.getElementById("baselineChartHint");
  if (!hint) return;
  const level = (payload && payload.level) || "province";
  const meta = CENSUS_LEVEL_LABELS[level] || CENSUS_LEVEL_LABELS.province;
  if (payload && payload.child_level && meta.children) {
    hint.textContent =
      `Tip: click a ${meta.singular} bar (or its label) to drill down ` +
      `into its ${meta.children}.`;
  } else {
    hint.textContent =
      "EA is the deepest level — no further drill-down available.";
  }
};

const drillIntoCensus = (row) => {
  if (!row || !lastChartPayload) return;
  const childLevel = lastChartPayload.child_level;
  if (!childLevel || !row.has_children) return;
  // The clicked geography becomes a parent filter for the child level.
  bcChartNav = {
    level: childLevel,
    parents: { ...bcChartNav.parents, [row.level]: row.value },
  };
  fetchCensusChartData();
};

// Widen the canvas box so bars stay legible when a level has many geographies;
// the wrap then scrolls horizontally. Narrow datasets keep the box at 100% so
// the chart simply fills the sub-tab.
const sizeCensusCanvasBox = (box, geographyCount) => {
  if (!box) return;
  const wrap = box.closest(".baseline-chart-wrap");
  if (!wrap) return;
  const minPerGeography = 64;
  const needed = geographyCount * minPerGeography;
  box.style.width = needed > wrap.clientWidth ? `${needed}px` : "100%";
};

// Render the chart for one sub-tab. "coverage" sub-tabs (Population,
// Households) chart estimate + enumerated bars with a coverage line; "counts"
// sub-tabs (Vital Events) chart each series' enumerated count as its own bar,
// drawn alongside each other. Each geography is a single x-axis category, so a
// bar click drills straight in.
const renderSubtabChart = (subtab) => {
  const canvas = document.getElementById(subtab.canvasId);
  if (!canvas || typeof Chart === "undefined" || !lastChartPayload) return;

  destroyChartForCanvas(canvas);

  const results = lastChartPayload.results || [];
  const labels = results.map((row) => row.label || "—");
  const hasCoverage = subtab.mode === "coverage";
  const datasets = [];

  if (hasCoverage) {
    datasets.push({
      type: "bar",
      label: "Estimate",
      data: results.map((row) => row[`${subtab.indicatorKey}_estimate`]),
      backgroundColor: "#2563eb",
      borderRadius: 4,
      yAxisID: "y",
    });
    datasets.push({
      type: "bar",
      label: "Enumerated",
      data: results.map((row) => row[`${subtab.indicatorKey}_enumerated`]),
      backgroundColor: "#dc2626",
      borderRadius: 4,
      yAxisID: "y",
    });
    datasets.push({
      type: "line",
      label: "Coverage",
      data: results.map((row) => row[`${subtab.indicatorKey}_coverage`]),
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
    });
  } else {
    subtab.series.forEach((series) => {
      datasets.push({
        type: "bar",
        label: series.label,
        data: results.map((row) => row[`${series.key}_enumerated`]),
        backgroundColor: series.color,
        borderRadius: 4,
        yAxisID: "y",
      });
    });
  }

  const scales = {
    x: {
      grid: { display: false },
      ticks: { autoSkip: false, maxRotation: 45, minRotation: 0 },
    },
    y: {
      beginAtZero: true,
      position: "left",
      title: { display: true, text: "Counts" },
      ticks: { callback: (value) => formatCountValue(value) },
    },
  };
  if (hasCoverage) {
    scales.yCoverage = {
      beginAtZero: true,
      suggestedMax: 100,
      position: "right",
      title: { display: true, text: "Coverage" },
      grid: { drawOnChartArea: false },
      ticks: { callback: (value) => formatCoverageValue(value) },
    };
  }

  sizeCensusCanvasBox(canvas.closest(".baseline-chart-canvas-box"), results.length);

  const chart = new Chart(canvas.getContext("2d"), {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      layout: { padding: { top: 22 } },
      onClick: (evt, elements) => {
        if (!elements || !elements.length) return;
        drillIntoCensus(results[elements[0].index]);
      },
      onHover: (evt, elements) => {
        const target = evt && evt.native && evt.native.target;
        if (!target) return;
        const row = elements && elements.length ? results[elements[0].index] : null;
        target.style.cursor = row && row.has_children ? "pointer" : "default";
      },
      plugins: {
        legend: {
          display: true,
          position: "top",
          labels: { boxWidth: 12, boxHeight: 12 },
        },
        tooltip: {
          callbacks: {
            title(items) {
              if (!items.length) return "";
              const row = results[items[0].dataIndex] || {};
              return `${row.label || "—"} — ${subtab.label}`;
            },
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
      scales,
    },
    plugins: [enumerationCoverageLabelsPlugin],
  });
  chartInstances.set(canvas, chart);
};

// Render whichever sub-tab is currently active from the cached payload.
const renderActiveSubtabChart = () => {
  if (
    !lastChartPayload ||
    !lastChartPayload.results ||
    lastChartPayload.results.length === 0
  ) {
    return;
  }
  renderSubtabChart(censusSubtabByKey(bcChartActiveSubtab));
};

const fetchCensusChartData = () => {
  const url = getCensusChartUrl();
  if (!url) return Promise.resolve();

  const requestId = ++chartFetchRequestId;
  setCensusChartOverlay("loading");

  const params = new URLSearchParams();
  params.set("level", bcChartNav.level);
  Object.entries(bcChartNav.parents).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  if (bcChartFilterState.location_level && bcChartFilterState.location_value) {
    params.set("location_level", bcChartFilterState.location_level);
    params.set("location_value", bcChartFilterState.location_value);
  }
  if (bcChartFilterState.start) params.set("start", bcChartFilterState.start);
  if (bcChartFilterState.end) params.set("end", bcChartFilterState.end);
  if (bcChartFilterState.preset) params.set("preset", bcChartFilterState.preset);
  params.set("bc_current", bcChartFilterState.current ? "1" : "0");

  return fetch(`${url}?${params.toString()}`, {
    method: "GET",
    headers: { "X-Requested-With": "XMLHttpRequest" },
    credentials: "same-origin",
  })
    .then((res) => {
      if (!res.ok) throw new Error(`Census chart fetch failed: ${res.status}`);
      return res.json();
    })
    .then((payload) => {
      if (requestId !== chartFetchRequestId) return;
      if (payload.error) throw new Error(payload.error);
      lastChartPayload = payload;
      bcChartNav.level = payload.level || bcChartNav.level;
      renderCensusBreadcrumb(payload);
      renderCensusChartHint(payload);
      if (!payload.results || payload.results.length === 0) {
        CENSUS_SUBTABS.forEach((subtab) => {
          destroyChartForCanvas(document.getElementById(subtab.canvasId));
        });
        setCensusChartOverlay("empty");
        return;
      }
      setCensusChartOverlay(null);
      // One fetch feeds every sub-tab; only the visible one is drawn now,
      // the rest re-render from this payload when their sub-tab is shown.
      renderActiveSubtabChart();
    })
    .catch((err) => {
      if (requestId !== chartFetchRequestId) return;
      console.error("[baseline-census] census chart fetch failed", err);
      setCensusChartOverlay("error");
    });
};

const initChartFilters = () => {
  if (chartFiltersBound) return;
  chartFiltersBound = true;

  const levelSelect = document.getElementById("chartLocationLevel");
  const valueSelect = document.getElementById("chartLocationValue");
  const currentCensusCheckbox = document.getElementById("chartCurrentCensus");

  if (currentCensusCheckbox) {
    bcChartFilterState.current = currentCensusCheckbox.checked;
    currentCensusCheckbox.addEventListener("change", function () {
      bcChartFilterState.current = this.checked;
      resetChartNav();
      fetchCensusChartData();
    });
  }

  if (levelSelect) {
    updateChartLocationValueOptions("national");
    levelSelect.addEventListener("change", function () {
      updateChartLocationValueOptions(this.value);
      bcChartFilterState.location_level = this.value;
      bcChartFilterState.location_value = "";
      resetChartNav();
      fetchCensusChartData();
    });
  }

  if (valueSelect) {
    valueSelect.addEventListener("change", function () {
      bcChartFilterState.location_value = this.value;
      resetChartNav();
      fetchCensusChartData();
    });
  }

  ["chartTimeAll", "chartTime30", "chartTime7", "chartTime24"].forEach((id) => {
    const radio = document.getElementById(id);
    if (!radio) return;
    radio.addEventListener("change", function () {
      if (!this.checked) return;
      const presetMap = {
        chartTimeAll: "",
        chartTime30: "30",
        chartTime7: "7",
        chartTime24: "24",
      };
      const preset = presetMap[id] || "";
      bcChartFilterState.preset = preset;

      const startInput = document.getElementById("chartStartDatetime");
      const endInput = document.getElementById("chartEndDatetime");
      if (preset && startInput && endInput) {
        const now = new Date();
        const start = new Date(now);
        if (preset === "30") start.setDate(start.getDate() - 30);
        if (preset === "7") start.setDate(start.getDate() - 7);
        if (preset === "24") start.setHours(start.getHours() - 24);
        startInput.value = toDateString(start);
        endInput.value = toDateString(now);
        bcChartFilterState.start = startInput.value;
        bcChartFilterState.end = endInput.value;
      } else {
        bcChartFilterState.start = "";
        bcChartFilterState.end = "";
      }
      resetChartNav();
      fetchCensusChartData();
    });
  });

  const startInput = document.getElementById("chartStartDatetime");
  const endInput = document.getElementById("chartEndDatetime");

  if (startInput) {
    startInput.addEventListener("change", function () {
      bcChartFilterState.start = this.value;
      resetChartNav();
      fetchCensusChartData();
    });
  }

  if (endInput) {
    endInput.addEventListener("change", function () {
      bcChartFilterState.end = this.value;
      resetChartNav();
      fetchCensusChartData();
    });
  }
};

const wireChartBreadcrumbEvents = () => {
  const backBtn = document.getElementById("baselineChartBack");
  if (!backBtn) return;
  backBtn.addEventListener("click", () => {
    const crumbs = (lastChartPayload && lastChartPayload.breadcrumbs) || [];
    if (crumbs.length < 2) return;
    navigateCensusChartTo(crumbs[crumbs.length - 2]);
  });
};

let censusSubtabsBound = false;

// Switch the active Census Chart sub-tab. Filters and drill-down state are
// shared, so this only swaps the visible panel and redraws that sub-tab's
// chart from the cached payload — no refetch, no page reload.
const setActiveCensusSubtab = (subtabKey) => {
  const subtab = censusSubtabByKey(subtabKey);
  bcChartActiveSubtab = subtab.key;

  document.querySelectorAll("#censusChartTabs .census-chart-subtab").forEach((btn) => {
    const isActive = btn.dataset.censusSubtab === subtab.key;
    btn.classList.toggle("census-chart-subtab--active", isActive);
    btn.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  document
    .querySelectorAll("#censusChartSubpanels .census-chart-subpanel")
    .forEach((panel) => {
      panel.classList.toggle(
        "census-chart-subpanel--active",
        panel.dataset.subtab === subtab.key
      );
    });

  // The panel is now visible, so the canvas has real dimensions to draw into.
  renderActiveSubtabChart();
};

const initCensusChartSubtabs = () => {
  if (censusSubtabsBound) return;
  censusSubtabsBound = true;
  const tabs = document.getElementById("censusChartTabs");
  if (!tabs) return;
  tabs.addEventListener("click", (event) => {
    const btn = event.target.closest(".census-chart-subtab");
    if (!btn || !btn.dataset.censusSubtab) return;
    setActiveCensusSubtab(btn.dataset.censusSubtab);
  });
};

const initChartTab = async () => {
  if (chartTabInitialized) {
    resizeChartsForTab("chart");
    return;
  }
  chartTabInitialized = true;
  initChartFilters();
  initCensusChartSubtabs();
  wireChartBreadcrumbEvents();
  await fetchCensusChartData();
  resizeChartsForTab("chart");
};

const initBaselineCensusTab = async (tab) => {
  const normalizedTab = ["table", "chart"].includes(tab) ? tab : "overview";
  const started = perfNow();
  if (normalizedTab === "overview") {
    await initOverviewTab();
    perfLog("tab.init.overview", started);
    return;
  }
  if (normalizedTab === "table") {
    await initTableTab();
    perfLog("tab.init.table", started);
    return;
  }
  if (normalizedTab === "chart") {
    await initChartTab();
    perfLog("tab.init.chart", started);
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
