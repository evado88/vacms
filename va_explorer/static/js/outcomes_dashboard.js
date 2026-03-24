(function () {
  const app = document.getElementById("outcomesDashboardApp");
  if (!app) return;

  const perfNow = () => (
    window.performance && typeof window.performance.now === "function"
      ? window.performance.now()
      : Date.now()
  );
  const perfLog = (name, startMs, meta) => {
    const durationMs = perfNow() - startMs;
    if (meta) {
      console.log(`[perf][outcomes] ${name} ${durationMs.toFixed(2)}ms`, meta);
    } else {
      console.log(`[perf][outcomes] ${name} ${durationMs.toFixed(2)}ms`);
    }
  };

  const endpoints = {
    summary: app.dataset.summaryEndpoint,
    trend: app.dataset.trendEndpoint,
    birthOutcomes: app.dataset.birthOutcomesEndpoint,
    gestationalAge: app.dataset.gestationalAgeEndpoint,
    ancVisits: app.dataset.ancVisitsEndpoint,
    kpis: app.dataset.kpisEndpoint,
    placeOfBirth: app.dataset.placeOfBirthEndpoint,
    map: app.dataset.mapEndpoint,
  };

  const chartState = {
    trend: null,
    birthOutcomes: null,
    gestationalAge: null,
    ancVisits: null,
    placeOfBirth: null,
    birthOutcomesPayload: null,
    placeOfBirthPayload: null,
  };
  let mapSelection = { geography_level: "", geography_value: "" };

  const getEl = (id) => document.getElementById(id);
  const syncPoMapSectionHeight = () => {
    const mapSection = getEl("poMapSection");
    if (!mapSection) return;

    if (window.matchMedia("(max-width: 992px)").matches) {
      mapSection.style.removeProperty("height");
      return;
    }

    const leftCol = app.querySelector(".po-dashboard-column--left");
    const rightCol = app.querySelector(".po-dashboard-column--right");
    const panel = getEl("poPanelSection");
    if (!leftCol || !rightCol || !panel) return;

    const rightHeight = rightCol.getBoundingClientRect().height;
    const panelHeight = panel.getBoundingClientRect().height;
    const computedLeft = window.getComputedStyle(leftCol);
    const gap = parseFloat(computedLeft.rowGap || computedLeft.gap || "0") || 0;
    const available = Math.floor(rightHeight - panelHeight - gap);

    if (available > 0) {
      mapSection.style.height = `${available}px`;
    } else {
      mapSection.style.removeProperty("height");
    }
  };

  const filterElements = {
    form: getEl("poFiltersForm"),
    outcome: getEl("poFilterPregnancyOutcome"),
    preset: getEl("poFilterTimePreset"),
    start: getEl("poFilterStartDatetime"),
    end: getEl("poFilterEndDatetime"),
    mapViewSelect: getEl("poMapViewSelect"),
    mapViewHidden: getEl("poFilterMapView"),
    reset: getEl("poFiltersReset"),
    birthCount: getEl("poBirthModeCount"),
    birthPct: getEl("poBirthModePercentage"),
    placeCount: getEl("poPlaceBirthModeCount"),
    placePct: getEl("poPlaceBirthModePercentage"),
  };

  const getFilters = () => ({
    pregnancy_outcome: filterElements.outcome?.value || "",
    time_preset: filterElements.preset?.value || "all_time",
    start_datetime: filterElements.start?.value || "",
    end_datetime: filterElements.end?.value || "",
    map_view: filterElements.mapViewSelect?.value || "Province",
  });

  const getEffectiveSelection = () => {
    if (mapController && typeof mapController.getSelection === "function") {
      return mapController.getSelection();
    }
    return { ...mapSelection };
  };

  const buildParams = (filters) => {
    const params = new URLSearchParams();
    if (filters.pregnancy_outcome) params.set("pregnancy_outcome", filters.pregnancy_outcome);
    if (filters.time_preset && filters.time_preset !== "all_time") params.set("time_preset", filters.time_preset);
    if (filters.start_datetime) params.set("start_datetime", filters.start_datetime);
    if (filters.end_datetime) params.set("end_datetime", filters.end_datetime);
    if (filters.map_view && filters.map_view !== "Province") params.set("map_view", filters.map_view);
    const selection = getEffectiveSelection();
    if (selection.geography_level && selection.geography_value) {
      params.set("geography_level", selection.geography_level);
      params.set("geography_value", selection.geography_value);
    }
    return params;
  };

  const syncFilterHiddenFields = () => {
    if (filterElements.mapViewHidden && filterElements.mapViewSelect) {
      filterElements.mapViewHidden.value = filterElements.mapViewSelect.value;
    }
  };

  const syncUrl = (filters) => {
    const params = buildParams(filters);
    const currentTab = new URLSearchParams(window.location.search).get("tab");
    if (currentTab) params.set("tab", currentTab);
    const query = params.toString();
    const nextUrl = query ? `${window.location.pathname}?${query}` : window.location.pathname;
    window.history.replaceState({}, "", nextUrl);
  };

  const fetchJSON = async (url, filters) => {
    const params = buildParams(filters);
    const requestUrl = params.toString() ? `${url}?${params.toString()}` : url;
    const response = await fetch(requestUrl, {
      method: "GET",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error(`Request failed: ${requestUrl}`);
    return response.json();
  };

  const mapController =
    typeof window.createHierarchicalDashboardMap === "function"
      ? window.createHierarchicalDashboardMap({
          containerId: "poMapContainer",
          legendId: "poMapLegend",
          breadcrumbId: "poMapBreadcrumb",
          emptyStateId: "poMapEmpty",
          endpoint: endpoints.map,
          buildParams,
          onSelectionChange: (selection) => {
            mapSelection = {
              geography_level: selection?.geography_level || "",
              geography_value: selection?.geography_value || "",
            };
            refreshDataOnly().catch((err) => console.error(err));
          },
          includeSelectionInRequest: false,
          styleVariant: "va",
          noDataMessage: "No mapped outcomes in current filter range.",
        })
      : null;

  const setText = (id, value) => {
    const el = getEl(id);
    if (el) el.textContent = value;
  };

  const setEmptyState = (id, isEmpty) => {
    const el = getEl(id);
    if (el) el.hidden = !isEmpty;
  };

  const MIN_TREND_YEAR = 2024, MIN_TREND_MONTH = 12;
  const parseLabelDate = (label) => {
    const ym = String(label).match(/^(\d{4})-(\d{2})/);
    if (ym) return new Date(parseInt(ym[1]), parseInt(ym[2]) - 1, 1);
    const d = new Date(label);
    return isNaN(d) ? null : d;
  };
  const minTrendDate = new Date(MIN_TREND_YEAR, MIN_TREND_MONTH - 1, 1);

  const isValidDateValue = (value) => /^\d{4}-\d{2}-\d{2}$/.test(value || "");
  const shouldApplyDateRangeChange = () => {
    if (!filterElements.preset || filterElements.preset.value !== "custom") return true;
    const start = filterElements.start?.value || "";
    const end = filterElements.end?.value || "";
    if (!isValidDateValue(start) || !isValidDateValue(end)) return false;
    return start <= end;
  };
  const setupDateInputs = (inputs) => {
    const dateInputs = (inputs || []).filter(Boolean);
    if (!dateInputs.length) return;

    const iconOnlyThresholdPx = 170;
    const updateIconMode = () => {
      dateInputs.forEach((input) => {
        input.classList.toggle("is-icon-only", input.offsetWidth <= iconOnlyThresholdPx);
      });
    };

    dateInputs.forEach((input) => {
      input.addEventListener("pointerdown", () => {
        if (typeof input.showPicker === "function") {
          try {
            input.showPicker();
          } catch (_error) {
            input.focus();
          }
          return;
        }
        input.focus();
      });
    });

    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(updateIconMode);
      dateInputs.forEach((input) => observer.observe(input));
    } else {
      window.addEventListener("resize", updateIconMode);
    }
    updateIconMode();
  };

  const renderSummary = (data) => {
    const started = perfNow();
    setText("poCardLastDataUpdate", data.card_last_data_update || "N/A");
    setText("poCardLastEventDate", data.card_last_event_date || "N/A");
    setText("poCardNumberOfEvents", data.card_number_of_events ?? 0);
    setText("poCardMultipleBirthPct", `${data.card_multiple_birth_pct ?? 0}%`);
    setEmptyState("poSummaryEmpty", (data.card_number_of_events ?? 0) === 0);
    perfLog("render.summary", started);
  };

  const renderKpis = (data) => {
    const started = perfNow();
    setText("poCardMeanAge", data.mean_age ?? 0);
    setText("poCardHivPct", `${data.hiv_positive_pct ?? 0}%`);
    perfLog("render.kpis", started);
  };

  const ensureLineChart = () => {
    if (chartState.trend) return chartState.trend;
    const canvas = getEl("poTrendChart");
    if (!canvas || typeof Chart === "undefined") return null;
    chartState.trend = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: { labels: [], datasets: [{ label: "Pregnancy Outcomes", data: [], borderColor: "#2d6cdf", backgroundColor: "rgba(45,108,223,0.2)", pointRadius: 2, tension: 0.25, fill: false }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: true, position: "top" }, tooltip: { enabled: true } },
        scales: {
          x: { grid: { display: true, color: "rgba(148, 163, 184, 0.35)" } },
          y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Count" }, grid: { display: true, color: "rgba(148, 163, 184, 0.35)" } },
        },
      },
    });
    return chartState.trend;
  };

  const ensureBarChart = (key, canvasId, options) => {
    if (chartState[key]) return chartState[key];
    const canvas = getEl(canvasId);
    if (!canvas || typeof Chart === "undefined") return null;
    chartState[key] = new Chart(canvas.getContext("2d"), options);
    return chartState[key];
  };

  const renderTrend = (data) => {
    const started = perfNow();
    const chart = ensureLineChart();
    if (!chart) return;
    const rawLabels = data.labels || [];
    const rawData = data.data || [];
    const filtered = rawLabels.reduce(
      (acc, label, i) => {
        const d = parseLabelDate(label);
        if (d && d >= minTrendDate) { acc.labels.push(label); acc.data.push(rawData[i]); }
        return acc;
      },
      { labels: [], data: [] }
    );
    chart.data.labels = filtered.labels;
    chart.data.datasets[0].data = filtered.data;
    chart.update();
    const sum = filtered.data.reduce((acc, value) => acc + Number(value || 0), 0);
    setEmptyState("poTrendEmpty", sum === 0);
    perfLog("render.trend_chart", started, { points: filtered.data.length });
  };

  const currentBirthMode = () => (filterElements.birthPct?.checked ? "percentage" : "count");
  const currentPlaceMode = () => (filterElements.placePct?.checked ? "percentage" : "count");

  const renderBirthOutcomes = (payload) => {
    const started = perfNow();
    chartState.birthOutcomesPayload = payload;
    const chart = ensureBarChart("birthOutcomes", "poBirthOutcomesChart", {
      type: "pie",
      data: { labels: [], datasets: [{ label: "Birth Outcomes", data: [], backgroundColor: ["#2d6cdf", "#f46d43"], borderColor: ["#2d6cdf", "#f46d43"], borderWidth: 1 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: true, position: "bottom" }, tooltip: { enabled: true } },
      },
    });
    if (!chart) return;
    const mode = currentBirthMode();
    chart.data.labels = payload.labels || [];
    chart.data.datasets[0].data = mode === "percentage" ? (payload.percentage_data || []) : (payload.count_data || []);
    chart.data.datasets[0].label = mode === "percentage" ? "Birth Outcomes (%)" : "Birth Outcomes";
    chart.update();
    const sum = (payload.count_data || []).reduce((acc, value) => acc + Number(value || 0), 0);
    setEmptyState("poBirthOutcomesEmpty", sum === 0);
    perfLog("render.birth_outcomes_chart", started);
  };

  const renderGestationalAge = (payload) => {
    const started = perfNow();
    const chart = ensureBarChart("gestationalAge", "poGestationalAgeChart", {
      type: "bar",
      data: { labels: [], datasets: [{ label: "Outcomes", data: [], backgroundColor: ["#9ecae1", "#6baed6", "#3182bd", "#08519c"], borderColor: ["#9ecae1", "#6baed6", "#3182bd", "#08519c"], borderWidth: 1 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: true } },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Count" }, grid: { display: true, color: "rgba(148,163,184,0.35)" } },
        },
      },
    });
    if (!chart) return;
    chart.data.labels = payload.labels || [];
    chart.data.datasets[0].data = payload.data || [];
    chart.update();
    const sum = (payload.data || []).reduce((acc, value) => acc + Number(value || 0), 0);
    setEmptyState("poGestationalAgeEmpty", sum === 0);
    perfLog("render.gestational_age_chart", started);
  };

  const renderAncVisits = (payload) => {
    const started = perfNow();
    const chart = ensureBarChart("ancVisits", "poAncVisitsChart", {
      type: "bar",
      data: { labels: [], datasets: [{ label: "Outcomes", data: [], backgroundColor: "#4CAF50", borderColor: "#2E7D32", borderWidth: 1 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: true } },
        scales: {
          x: { grid: { display: false }, title: { display: true, text: "ANC Visits" } },
          y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Count" }, grid: { display: true, color: "rgba(148,163,184,0.35)" } },
        },
      },
    });
    if (!chart) return;
    chart.data.labels = payload.labels || [];
    chart.data.datasets[0].data = payload.data || [];
    chart.update();
    const sum = (payload.data || []).reduce((acc, value) => acc + Number(value || 0), 0);
    setEmptyState("poAncVisitsEmpty", sum === 0);
    perfLog("render.anc_visits_chart", started);
  };

  const renderPlaceOfBirth = (payload) => {
    const started = perfNow();
    chartState.placeOfBirthPayload = payload;
    const chart = ensureBarChart("placeOfBirth", "poPlaceOfBirthChart", {
      type: "bar",
      data: { labels: [], datasets: [{ label: "Place of Birth", data: [], backgroundColor: "#8e44ad", borderColor: "#6c3483", borderWidth: 1 }] },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: true } },
        scales: {
          x: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Count" }, grid: { display: true, color: "rgba(148,163,184,0.35)" } },
          y: { grid: { display: false } },
        },
      },
    });
    if (!chart) return;
    const mode = currentPlaceMode();
    chart.data.labels = payload.labels || [];
    chart.data.datasets[0].data = mode === "percentage" ? (payload.percentage_data || []) : (payload.count_data || []);
    chart.options.scales.x.title.text = mode === "percentage" ? "Percentage (%)" : "Count";
    chart.update();
    const sum = (payload.count_data || []).reduce((acc, value) => acc + Number(value || 0), 0);
    setEmptyState("poPlaceOfBirthEmpty", sum === 0);
    perfLog("render.place_of_birth_chart", started);
  };

  const refreshMapOnly = async () => {
    const started = perfNow();
    const filters = getFilters();
    syncFilterHiddenFields();
    syncUrl(filters);
    if (mapController) {
      await mapController.refresh(filters);
      syncPoMapSectionHeight();
      mapController.resize();
      perfLog("fetch.map_only", started, { mapView: filters.map_view });
      return;
    }
    const mapData = await fetchJSON(endpoints.map, filters);
    setEmptyState("poMapEmpty", (mapData.counts || []).length === 0);
    perfLog("fetch.map_only", started, { mapView: filters.map_view });
  };

  const refreshDataOnly = async () => {
    const started = perfNow();
    const filters = getFilters();
    syncFilterHiddenFields();
    syncUrl(filters);

    const [summary, trend, birthOutcomes, gestAge, anc, kpis, place] = await Promise.all([
      fetchJSON(endpoints.summary, filters),
      fetchJSON(endpoints.trend, filters),
      fetchJSON(endpoints.birthOutcomes, filters),
      fetchJSON(endpoints.gestationalAge, filters),
      fetchJSON(endpoints.ancVisits, filters),
      fetchJSON(endpoints.kpis, filters),
      fetchJSON(endpoints.placeOfBirth, filters),
    ]);

    renderSummary(summary);
    renderTrend(trend);
    renderBirthOutcomes(birthOutcomes);
    renderGestationalAge(gestAge);
    renderAncVisits(anc);
    renderKpis(kpis);
    renderPlaceOfBirth(place);
    perfLog("fetch.refresh_data_only", started);
  };

  const refreshAll = async () => {
    const started = perfNow();
    await refreshDataOnly();
    await refreshMapOnly();
    syncPoMapSectionHeight();
    if (mapController) mapController.resize();
    perfLog("fetch.refresh_all", started);
  };

  const bindEvents = () => {
    if (filterElements.form) {
      filterElements.form.addEventListener("submit", (event) => {
        event.preventDefault();
        refreshAll().catch((err) => console.error(err));
      });
    }

    if (filterElements.outcome) {
      filterElements.outcome.addEventListener("change", () =>
        refreshAll().catch((err) => console.error(err))
      );
    }
    if (filterElements.preset) {
      filterElements.preset.addEventListener("change", () => {
        if (!shouldApplyDateRangeChange()) return;
        refreshAll().catch((err) => console.error(err));
      });
    }
    if (filterElements.start) {
      filterElements.start.addEventListener("change", () => {
        if (!shouldApplyDateRangeChange()) return;
        refreshAll().catch((err) => console.error(err));
      });
    }
    if (filterElements.end) {
      filterElements.end.addEventListener("change", () => {
        if (!shouldApplyDateRangeChange()) return;
        refreshAll().catch((err) => console.error(err));
      });
    }

    if (filterElements.mapViewSelect) {
      filterElements.mapViewSelect.addEventListener("change", () => refreshMapOnly().catch((err) => console.error(err)));
    }

    if (filterElements.reset) {
      filterElements.reset.addEventListener("click", () => {
        if (filterElements.outcome) filterElements.outcome.value = "";
        if (filterElements.preset) filterElements.preset.value = "all_time";
        if (filterElements.start) filterElements.start.value = "";
        if (filterElements.end) filterElements.end.value = "";
        if (filterElements.mapViewSelect) filterElements.mapViewSelect.value = "Province";
        if (filterElements.birthCount) filterElements.birthCount.checked = true;
        if (filterElements.placeCount) filterElements.placeCount.checked = true;
        refreshAll().catch((err) => console.error(err));
      });
    }

    const rerenderBirth = () => {
      if (chartState.birthOutcomesPayload) renderBirthOutcomes(chartState.birthOutcomesPayload);
    };
    if (filterElements.birthCount) filterElements.birthCount.addEventListener("change", rerenderBirth);
    if (filterElements.birthPct) filterElements.birthPct.addEventListener("change", rerenderBirth);

    const rerenderPlace = () => {
      if (chartState.placeOfBirthPayload) renderPlaceOfBirth(chartState.placeOfBirthPayload);
    };
    if (filterElements.placeCount) filterElements.placeCount.addEventListener("change", rerenderPlace);
    if (filterElements.placePct) filterElements.placePct.addEventListener("change", rerenderPlace);
  };

  const init = async () => {
    const started = perfNow();
    document.querySelectorAll(".dashboard-shell[data-shell='outcomes'] [data-tab]").forEach((tabLink) => {
      tabLink.addEventListener("click", () => {
        console.log("[perf][outcomes] ui.tab_click", { tab: tabLink.dataset.tab });
      });
    });
    setupDateInputs([filterElements.start, filterElements.end]);
    const today = new Date().toISOString().slice(0, 10);
    if (filterElements.start && !filterElements.start.value) filterElements.start.value = today;
    if (filterElements.end && !filterElements.end.value) filterElements.end.value = today;
    bindEvents();
    await refreshAll();
    requestAnimationFrame(() => {
      syncPoMapSectionHeight();
      if (mapController) mapController.resize();
    });
    perfLog("initial.page_bootstrap", started);
  };

  const resizeVisuals = () => {
    syncPoMapSectionHeight();
    if (chartState.trend) chartState.trend.resize();
    if (chartState.birthOutcomes) chartState.birthOutcomes.resize();
    if (chartState.gestationalAge) chartState.gestationalAge.resize();
    if (chartState.ancVisits) chartState.ancVisits.resize();
    if (chartState.placeOfBirth) chartState.placeOfBirth.resize();
    if (mapController) mapController.resize();
  };

  const pane = app.closest(".tab-pane");
  if (pane && !pane.classList.contains("show")) {
    let initializedFromTab = false;
    const isPaneActivationEvent = (event) => {
      if (!event) return false;
      if (event.type === "dashboard:refresh-tab") {
        const detail = event.detail || {};
        const shell = detail.shell || "";
        const tab = detail.tab || "";
        const paneTab = pane.dataset.tabPanel || "";
        const paneShell = app.closest(".dashboard-shell")?.dataset?.shell || "";
        if (shell && paneShell && shell !== paneShell) return false;
        return !!paneTab && tab === paneTab;
      }
      const targetSelector =
        event.target?.getAttribute("data-bs-target") ||
        event.target?.getAttribute("data-target");
      return targetSelector === `#${pane.id}`;
    };
    const activateHandler = (event) => {
      if (!isPaneActivationEvent(event)) return;
      if (!initializedFromTab) {
        initializedFromTab = true;
        init()
          .then(() => setTimeout(resizeVisuals, 200))
          .catch((err) => console.error(err));
      } else {
        setTimeout(resizeVisuals, 200);
      }
    };
    if (window.jQuery) {
      window.jQuery(document).on("shown.bs.tab", activateHandler);
    } else {
      document.addEventListener("shown.bs.tab", activateHandler);
    }
    document.addEventListener("dashboard:refresh-tab", activateHandler);
    return;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      init()
        .then(() => setTimeout(resizeVisuals, 200))
        .catch((err) => console.error(err));
    });
  } else {
    init()
      .then(() => setTimeout(resizeVisuals, 200))
      .catch((err) => console.error(err));
  }
})();
