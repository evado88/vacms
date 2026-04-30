(function () {
  const app = document.getElementById("rumorDashboardApp");
  if (!app) return;

  const endpoints = {
    summary: app.dataset.summaryEndpoint,
    trend: app.dataset.trendEndpoint,
    detection: app.dataset.detectionEndpoint,
    followUp: app.dataset.followUpEndpoint,
    map: app.dataset.mapEndpoint,
    wordCloud: app.dataset.wordCloudEndpoint,
  };

  const chartState = {
    trend: null,
    detection: null,
  };
  let mapSelection = { geography_level: "", geography_value: "" };

  const getEl = (id) => document.getElementById(id);

  const filterElements = {
    form: getEl("rumorFiltersForm"),
    preset: getEl("rumorFilterTimePreset"),
    start: getEl("rumorFilterStartDatetime"),
    end: getEl("rumorFilterEndDatetime"),
    mapViewSelect: getEl("rumorMapViewSelect"),
    mapViewHidden: getEl("rumorFilterMapView"),
    reset: getEl("rumorFiltersReset"),
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
          containerId: "rumorMapContainer",
          legendId: "rumorMapLegend",
          breadcrumbId: "rumorMapBreadcrumb",
          emptyStateId: "rumorMapEmpty",
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
          noDataMessage: "No mapped rumor events in current filter range.",
          userCanViewPII,
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
          try { input.showPicker(); } catch (_e) { input.focus(); }
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

  // ── Summary cards ──────────────────────────────────────────────────────────
  const renderSummary = (data) => {
    setText("rumorCardLastDataUpdate", data.card_last_data_update || "N/A");
    setText("rumorCardNumberOfEvents", data.card_number_of_events ?? 0);
    setText("rumorCardProvincesReporting", data.card_provinces_reporting ?? 0);
    setText("rumorCardEasReporting", data.card_eas_reporting ?? 0);
    setText("rumorCardEnumerators", data.card_enumerators ?? 0);
    setText("rumorCardTopSource", data.card_top_source || "—");
    setEmptyState("rumorSummaryEmpty", (data.card_number_of_events ?? 0) === 0);
  };

  // ── Trend line chart ───────────────────────────────────────────────────────
  const ensureLineChart = () => {
    if (chartState.trend) return chartState.trend;
    const canvas = getEl("rumorTrendChart");
    if (!canvas || typeof Chart === "undefined") return null;
    chartState.trend = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: [],
        datasets: [{
          label: "Rumor Events",
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
          x: { title: { display: true, text: "Month" }, grid: { display: true, color: "rgba(148,163,184,0.35)" } },
          y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Count of rumor events" }, grid: { display: true, color: "rgba(148,163,184,0.35)" } },
        },
      },
    });
    return chartState.trend;
  };

  const renderTrend = (data) => {
    const chart = ensureLineChart();
    if (!chart) return;
    chart.data.labels = data.labels || [];
    chart.data.datasets[0].data = data.data || [];
    chart.update();
    const sum = (data.data || []).reduce((acc, v) => acc + Number(v || 0), 0);
    setEmptyState("rumorTrendEmpty", sum === 0);
  };

  // ── Source distribution donut ──────────────────────────────────────────────
  const SOURCE_COLORS = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#b07aa1"];

  const ensureDonutChart = () => {
    if (chartState.detection) return chartState.detection;
    const canvas = getEl("rumorDetectionChart");
    if (!canvas || typeof Chart === "undefined") return null;
    chartState.detection = new Chart(canvas.getContext("2d"), {
      type: "doughnut",
      data: { labels: [], datasets: [{ data: [], backgroundColor: SOURCE_COLORS, borderWidth: 1 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: true, position: "right" },
          tooltip: { enabled: true },
        },
      },
    });
    return chartState.detection;
  };

  const renderDetection = (payload) => {
    const chart = ensureDonutChart();
    if (!chart) return;
    chart.data.labels = payload.labels || [];
    chart.data.datasets[0].data = payload.data || [];
    chart.update();
    const sum = (payload.data || []).reduce((acc, v) => acc + Number(v || 0), 0);
    setEmptyState("rumorDetectionEmpty", sum === 0);
  };

  // ── Completeness stats ─────────────────────────────────────────────────────
  const renderFollowUp = (payload) => {
    const total = payload.total ?? 0;
    setText("rumorCompRecent7", payload.recent_7_days ?? "—");
    setText("rumorCompRecent30", payload.recent_30_days ?? "—");
    setText("rumorCompWithGps", payload.with_gps ?? "—");
    setText("rumorCompMissingGps", payload.missing_gps ?? "—");
    setText("rumorCompMissingDesc", payload.missing_description ?? "—");
    setText("rumorCompMissingSource", payload.missing_source ?? "—");
    setEmptyState("rumorFollowUpEmpty", total === 0);
  };

  // ── Word cloud ─────────────────────────────────────────────────────────────
  const renderWordCloud = (payload) => {
    const container = getEl("rumorWordCloud");
    if (!container) return;
    const words = payload.words || [];
    setEmptyState("rumorWordCloudEmpty", words.length === 0);
    if (!words.length) { container.innerHTML = ""; return; }

    const maxVal = words.reduce((m, w) => Math.max(m, w.value), 1);
    const minVal = words.reduce((m, w) => Math.min(m, w.value), maxVal);
    const range = maxVal - minVal || 1;
    const MIN_EM = 0.75;
    const MAX_EM = 2.4;

    const html = words
      .map((w) => {
        const norm = (w.value - minVal) / range;
        const size = (MIN_EM + norm * (MAX_EM - MIN_EM)).toFixed(2);
        const opacity = (0.55 + norm * 0.45).toFixed(2);
        const escaped = w.text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
        return `<span class="rumor-wc-word" style="font-size:${size}em;opacity:${opacity}" title="${escaped}: ${w.value}">${escaped}</span>`;
      })
      .join(" ");
    container.innerHTML = html;
  };

  // ── Map ────────────────────────────────────────────────────────────────────
  const syncRumorMapSectionHeight = () => {
    const mapSection = getEl("rumorMapSection");
    if (mapSection) mapSection.style.removeProperty("height");
  };

  // ── Refresh helpers ────────────────────────────────────────────────────────
  const refreshMapOnly = async () => {
    const filters = getFilters();
    syncFilterHiddenFields();
    syncUrl(filters);
    if (mapController) {
      await mapController.refresh(filters);
      syncRumorMapSectionHeight();
      mapController.resize();
      return;
    }
    const mapData = await fetchJSON(endpoints.map, filters);
    setEmptyState("rumorMapEmpty", (mapData.counts || []).length === 0);
  };

  const refreshDataOnly = async () => {
    const filters = getFilters();
    syncFilterHiddenFields();
    syncUrl(filters);

    const [summary, trend, detection, followUp, wordCloud] = await Promise.all([
      fetchJSON(endpoints.summary, filters),
      fetchJSON(endpoints.trend, filters),
      fetchJSON(endpoints.detection, filters),
      fetchJSON(endpoints.followUp, filters),
      fetchJSON(endpoints.wordCloud, filters),
    ]);

    renderSummary(summary);
    renderTrend(trend);
    renderDetection(detection);
    renderFollowUp(followUp);
    renderWordCloud(wordCloud);
  };

  const refreshAll = async () => {
    await refreshDataOnly();
    await refreshMapOnly();
    syncRumorMapSectionHeight();
    if (mapController) mapController.resize();
  };

  // ── Event bindings ─────────────────────────────────────────────────────────
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

  // ── Init ───────────────────────────────────────────────────────────────────
  const resizeVisuals = () => {
    syncRumorMapSectionHeight();
    if (chartState.trend) chartState.trend.resize();
    if (chartState.detection) chartState.detection.resize();
    if (mapController) mapController.resize();
  };

  const init = async () => {
    setupDateInputs([filterElements.start, filterElements.end]);
    bindEvents();
    await refreshAll();
    requestAnimationFrame(() => {
      syncRumorMapSectionHeight();
      if (mapController) mapController.resize();
    });
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
