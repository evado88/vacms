(function () {
  const app = document.getElementById("pregnancyDashboardApp");
  if (!app) return;

  const endpoints = {
    summary: app.dataset.summaryEndpoint,
    trend: app.dataset.trendEndpoint,
    gestationalAge: app.dataset.gestationalAgeEndpoint,
    ancVisits: app.dataset.ancVisitsEndpoint,
    map: app.dataset.mapEndpoint,
  };

  const chartState = {
    trend: null,
    gestationalAge: null,
    ancVisits: null,
  };
  let mapSelection = { geography_level: "", geography_value: "" };

  const getEl = (id) => document.getElementById(id);
  const syncPeMapSectionHeight = () => {
    // In the pe-content-row flex layout, CSS align-items:stretch handles
    // equal column heights automatically — no JS height calculation needed.
    const mapSection = getEl("peMapSection");
    if (mapSection) mapSection.style.removeProperty("height");
  };

  const filterElements = {
    form: getEl("peFiltersForm"),
    preset: getEl("peFilterTimePreset"),
    start: getEl("peFilterStartDatetime"),
    end: getEl("peFilterEndDatetime"),
    mapViewSelect: getEl("peMapViewSelect"),
    mapViewHidden: getEl("peFilterMapView"),
    reset: getEl("peFiltersReset"),
  };

  const getFilters = () => ({
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
    if (filters.time_preset && filters.time_preset !== "all_time") {
      params.set("time_preset", filters.time_preset);
    }
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

  const userCanViewPII = (app.dataset.userPii || "").toLowerCase() === "true";
  const mapController =
    typeof window.createHierarchicalDashboardMap === "function"
      ? window.createHierarchicalDashboardMap({
          containerId: "peMapContainer",
          legendId: "peMapLegend",
          breadcrumbId: "peMapBreadcrumb",
          emptyStateId: "peMapEmpty",
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
          noDataMessage: "No mapped pregnancy events in current filter range.",
          userCanViewPII,
          gpsFollowsDrilldown: true,
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
    setText("peCardLastDataUpdate", data.card_last_data_update || "N/A");
    setText("peCardLastEventDate", data.card_last_event_date || "N/A");
    setText("peCardNumberOfEvents", data.card_number_of_events ?? 0);
    setText("peCardMeanAge", data.card_mean_age ?? 0);
    setEmptyState("peSummaryEmpty", (data.card_number_of_events ?? 0) === 0);
  };

  const ensureLineChart = () => {
    if (chartState.trend) return chartState.trend;
    const canvas = getEl("peTrendChart");
    if (!canvas || typeof Chart === "undefined") return null;
    chartState.trend = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: [],
        datasets: [{
          label: "Pregnancy Events",
          data: [],
          borderColor: "#2d6cdf",
          backgroundColor: "rgba(45,108,223,0.2)",
          pointRadius: 2,
          tension: 0.25,
          fill: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: true, position: "top" },
          tooltip: { enabled: true },
          title: { display: false },
        },
        scales: {
          x: { title: { display: true, text: "Month" }, grid: { display: true, color: "rgba(148, 163, 184, 0.35)" } },
          y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Count of pregnancy events" }, grid: { display: true, color: "rgba(148, 163, 184, 0.35)" } },
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
    const chart = ensureLineChart();
    if (!chart) return;
    chart.data.labels = data.labels || [];
    chart.data.datasets[0].data = data.data || [];
    chart.update();
    const sum = (data.data || []).reduce((acc, value) => acc + Number(value || 0), 0);
    setEmptyState("peTrendEmpty", sum === 0);
  };

  const renderGestationalAge = (payload) => {
    const chart = ensureBarChart("gestationalAge", "peGestationalAgeChart", {
      type: "bar",
      data: {
        labels: [],
        datasets: [{
          label: "Pregnancy Events",
          data: [],
          backgroundColor: "#6baed6",
          borderColor: "#3182bd",
          borderWidth: 1,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: true } },
        scales: {
          x: { title: { display: true, text: "Gestational age at detection (weeks)" }, grid: { display: false } },
          y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Number of pregnancy events" }, grid: { display: true, color: "rgba(148,163,184,0.35)" } },
        },
      },
    });
    if (!chart) return;
    chart.data.labels = payload.labels || [];
    chart.data.datasets[0].data = payload.data || [];
    chart.update();
    const sum = (payload.data || []).reduce((acc, value) => acc + Number(value || 0), 0);
    setEmptyState("peGestationalAgeEmpty", sum === 0);
  };

  const renderAncVisits = (payload) => {
    const chart = ensureBarChart("ancVisits", "peAncVisitsChart", {
      type: "scatter",
      data: {
        datasets: [{
          label: "Pregnancy Events",
          data: [],
          pointRadius: 3,
          pointHoverRadius: 4,
          borderWidth: 0,
          backgroundColor: "rgba(46, 125, 50, 0.45)",
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: true } },
        scales: {
          x: {
            type: "linear",
            min: 1,
            max: 40,
            ticks: { precision: 0 },
            title: { display: true, text: "Gestational age at detection (weeks)" },
            grid: { display: true, color: "rgba(148,163,184,0.25)" },
          },
          y: {
            beginAtZero: true,
            max: 15,
            ticks: { precision: 0 },
            title: { display: true, text: "ANC visit count" },
            grid: { display: true, color: "rgba(148,163,184,0.35)" },
          },
        },
      },
    });

    if (!chart) return;

    const points = (payload.points || []).map((point, idx) => {
      const jitter = ((idx % 5) - 2) * 0.06;
      return { x: Number(point.x || 0) + jitter, y: Number(point.y || 0) };
    });

    chart.data.datasets[0].data = points;
    if (payload.x_min != null && payload.x_max != null) {
      chart.options.scales.x.min = Number(payload.x_min);
      chart.options.scales.x.max = Number(payload.x_max);
    }
    chart.update();
    setEmptyState("peAncVisitsEmpty", points.length === 0);
  };

  const refreshMapOnly = async () => {
    const filters = getFilters();
    syncFilterHiddenFields();
    syncUrl(filters);
    if (mapController) {
      await mapController.refresh(filters);
      syncPeMapSectionHeight();
      mapController.resize();
      return;
    }
    const mapData = await fetchJSON(endpoints.map, filters);
    setEmptyState("peMapEmpty", (mapData.counts || []).length === 0);
  };

  const refreshDataOnly = async () => {
    const filters = getFilters();
    syncFilterHiddenFields();
    syncUrl(filters);

    const [summary, trend, gestAge, anc] = await Promise.all([
      fetchJSON(endpoints.summary, filters),
      fetchJSON(endpoints.trend, filters),
      fetchJSON(endpoints.gestationalAge, filters),
      fetchJSON(endpoints.ancVisits, filters),
    ]);

    renderSummary(summary);
    renderTrend(trend);
    renderGestationalAge(gestAge);
    renderAncVisits(anc);
  };

  const refreshAll = async () => {
    await refreshDataOnly();
    await refreshMapOnly();
    syncPeMapSectionHeight();
    if (mapController) mapController.resize();
  };

  const bindEvents = () => {
    if (filterElements.form) {
      filterElements.form.addEventListener("submit", (event) => {
        event.preventDefault();
        refreshAll().catch((err) => console.error(err));
      });
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
        if (filterElements.preset) filterElements.preset.value = "all_time";
        if (filterElements.start) filterElements.start.value = "";
        if (filterElements.end) filterElements.end.value = "";
        if (filterElements.mapViewSelect) filterElements.mapViewSelect.value = "Province";
        refreshAll().catch((err) => console.error(err));
      });
    }
  };

  const init = async () => {
    setupDateInputs([filterElements.start, filterElements.end]);
    const today = new Date().toISOString().slice(0, 10);
    if (filterElements.start && !filterElements.start.value) filterElements.start.value = today;
    if (filterElements.end && !filterElements.end.value) filterElements.end.value = today;
    bindEvents();
    await refreshAll();
    requestAnimationFrame(() => {
      syncPeMapSectionHeight();
      if (mapController) mapController.resize();
    });
  };

  const resizeVisuals = () => {
    syncPeMapSectionHeight();
    if (chartState.trend) chartState.trend.resize();
    if (chartState.gestationalAge) chartState.gestationalAge.resize();
    if (chartState.ancVisits) chartState.ancVisits.resize();
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
          .then(() => setTimeout(resizeVisuals, 0))
          .catch((err) => console.error(err));
      } else {
        setTimeout(resizeVisuals, 0);
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
        .then(() => setTimeout(resizeVisuals, 0))
        .catch((err) => console.error(err));
    });
  } else {
    init()
      .then(() => setTimeout(resizeVisuals, 0))
      .catch((err) => console.error(err));
  }
})();
