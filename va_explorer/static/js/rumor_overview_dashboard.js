(function () {
  const app = document.getElementById("rumorOverviewDashboardApp");
  if (!app) return;

  const endpoints = {
    summary: app.dataset.summaryEndpoint,
    trend: app.dataset.trendEndpoint,
    sourceByProvince: app.dataset.sourceByProvinceEndpoint,
    affectedPop: app.dataset.affectedPopEndpoint,
    map: app.dataset.mapEndpoint,
  };

  const chartState = { trend: null, sourceByProvince: null, affectedPop: null };
  let mapSelection = { geography_level: "", geography_value: "" };

  const getEl = (id) => document.getElementById(id);

  const filterElements = {
    form: getEl("rumorOverviewFiltersForm"),
    preset: getEl("rumorOverviewFilterTimePreset"),
    start: getEl("rumorOverviewFilterStartDatetime"),
    end: getEl("rumorOverviewFilterEndDatetime"),
    mapViewSelect: getEl("rumorOverviewMapViewSelect"),
    mapViewHidden: getEl("rumorOverviewFilterMapView"),
    reset: getEl("rumorOverviewFiltersReset"),
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
    if (filters.time_preset && filters.time_preset !== "all_time") params.set("time_preset", filters.time_preset);
    if (filters.start_datetime) params.set("start_datetime", filters.start_datetime);
    if (filters.end_datetime) params.set("end_datetime", filters.end_datetime);
    if (filters.map_view && filters.map_view !== "Province") params.set("map_view", filters.map_view);
    const sel = getEffectiveSelection();
    if (sel.geography_level && sel.geography_value) {
      params.set("geography_level", sel.geography_level);
      params.set("geography_value", sel.geography_value);
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
    window.history.replaceState({}, "", query ? `${window.location.pathname}?${query}` : window.location.pathname);
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
          containerId: "rumorOverviewMapContainer",
          legendId: "rumorOverviewMapLegend",
          breadcrumbId: "rumorOverviewMapBreadcrumb",
          emptyStateId: "rumorOverviewMapEmpty",
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

  const setText = (id, value) => { const el = getEl(id); if (el) el.textContent = value; };
  const setEmptyState = (id, isEmpty) => { const el = getEl(id); if (el) el.hidden = !isEmpty; };

  const isValidDateValue = (v) => /^\d{4}-\d{2}-\d{2}$/.test(v || "");
  const shouldApplyDateRangeChange = () => {
    if (!filterElements.preset || filterElements.preset.value !== "custom") return true;
    const s = filterElements.start?.value || "";
    const e = filterElements.end?.value || "";
    return isValidDateValue(s) && isValidDateValue(e) && s <= e;
  };

  const setupDateInputs = (inputs) => {
    const dateInputs = (inputs || []).filter(Boolean);
    if (!dateInputs.length) return;
    const iconOnlyPx = 170;
    const updateIconMode = () => dateInputs.forEach((i) => i.classList.toggle("is-icon-only", i.offsetWidth <= iconOnlyPx));
    dateInputs.forEach((input) => {
      input.addEventListener("pointerdown", () => {
        if (typeof input.showPicker === "function") { try { input.showPicker(); } catch (_e) { input.focus(); } return; }
        input.focus();
      });
    });
    if (typeof ResizeObserver !== "undefined") {
      const obs = new ResizeObserver(updateIconMode);
      dateInputs.forEach((i) => obs.observe(i));
    } else {
      window.addEventListener("resize", updateIconMode);
    }
    updateIconMode();
  };

  // ── Summary cards ──────────────────────────────────────────────────────────
  const renderSummary = (data) => {
    setText("rumorOverviewCardLastDataUpdate", data.card_last_data_update || "N/A");
    setText("rumorOverviewCardNumberOfEvents", data.card_number_of_events ?? 0);
    setText("rumorOverviewCardProvincesReporting", data.card_provinces_reporting ?? 0);
    setText("rumorOverviewCardEasReporting", data.card_eas_reporting ?? 0);
    setText("rumorOverviewCardTopSource", data.card_top_source || "—");
    setEmptyState("rumorOverviewSummaryEmpty", (data.card_number_of_events ?? 0) === 0);
  };

  // ── Multi-line trend (top 5 rumor descriptions) ────────────────────────────
  const TREND_PALETTE = ["#d73027", "#4575b4", "#4CAF50", "#f46d43", "#8e44ad"];

  const ensureTrendChart = () => {
    if (chartState.trend) return chartState.trend;
    const canvas = getEl("rumorOverviewTrendChart");
    if (!canvas || typeof Chart === "undefined") return null;
    chartState.trend = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: { labels: [], datasets: [] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: true, position: "top" },
          tooltip: { enabled: true },
        },
        scales: {
          x: { title: { display: true, text: "Month" }, grid: { display: true, color: "rgba(148,163,184,0.35)" } },
          y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Count" }, grid: { display: true, color: "rgba(148,163,184,0.35)" } },
        },
      },
    });
    return chartState.trend;
  };

  const renderTrend = (data) => {
    const chart = ensureTrendChart();
    if (!chart) return;
    const labels = data.periods || data.labels || [];
    const series = data.series || [];
    chart.data.labels = labels;
    chart.data.datasets = series.map((s, i) => ({
      label: s.name,
      data: s.values || [],
      borderColor: TREND_PALETTE[i % TREND_PALETTE.length],
      backgroundColor: TREND_PALETTE[i % TREND_PALETTE.length],
      pointRadius: 2,
      tension: 0.25,
      fill: false,
    }));
    chart.update();
    const sum = series.flatMap((s) => s.values || []).reduce((a, v) => a + Number(v || 0), 0);
    setEmptyState("rumorOverviewTrendEmpty", !series.length || sum === 0);
  };

  // ── Source × Province stacked bar ─────────────────────────────────────────
  const ensureSourceByProvinceChart = () => {
    if (chartState.sourceByProvince) return chartState.sourceByProvince;
    const canvas = getEl("rumorOverviewSourceByProvinceChart");
    if (!canvas || typeof Chart === "undefined") return null;
    chartState.sourceByProvince = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: { labels: [], datasets: [] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: true, position: "top" }, tooltip: { enabled: true } },
        scales: {
          x: { stacked: true, title: { display: true, text: "Province" }, ticks: { maxRotation: 45 }, grid: { display: false } },
          y: { stacked: true, beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Rumor events" }, grid: { display: true, color: "rgba(148,163,184,0.35)" } },
        },
      },
    });
    return chartState.sourceByProvince;
  };

  const renderSourceByProvince = (data) => {
    const chart = ensureSourceByProvinceChart();
    if (!chart) return;
    chart.data.labels = data.provinces || [];
    chart.data.datasets = (data.series || []).map((s) => ({
      label: s.label,
      data: s.data || [],
      backgroundColor: s.backgroundColor || "#999",
      borderWidth: 0,
    }));
    chart.update();
    const sum = (data.series || []).flatMap((s) => s.data || []).reduce((a, v) => a + Number(v || 0), 0);
    setEmptyState("rumorOverviewSourceByProvinceEmpty", sum === 0);
  };

  // ── Affected Population dual-axis ─────────────────────────────────────────
  const ensureAffectedPopChart = () => {
    if (chartState.affectedPop) return chartState.affectedPop;
    const canvas = getEl("rumorOverviewAffectedPopChart");
    if (!canvas || typeof Chart === "undefined") return null;
    chartState.affectedPop = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: [],
        datasets: [
          {
            type: "line",
            label: "Rumor Events",
            data: [],
            borderColor: "#2d6cdf",
            backgroundColor: "rgba(45,108,223,0.15)",
            pointRadius: 2,
            tension: 0.25,
            fill: false,
            yAxisID: "y",
          },
          {
            type: "bar",
            label: "People Affected",
            data: [],
            backgroundColor: "rgba(231,76,60,0.55)",
            borderColor: "#e74c3c",
            borderWidth: 1,
            yAxisID: "y1",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: true, position: "top" }, tooltip: { enabled: true } },
        scales: {
          x: { title: { display: true, text: "Month" }, grid: { display: false } },
          y: {
            position: "left",
            beginAtZero: true,
            ticks: { precision: 0 },
            title: { display: true, text: "Events" },
            grid: { display: true, color: "rgba(148,163,184,0.35)" },
          },
          y1: {
            position: "right",
            beginAtZero: true,
            ticks: { precision: 0 },
            title: { display: true, text: "Affected" },
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
    return chartState.affectedPop;
  };

  const renderAffectedPop = (data) => {
    const chart = ensureAffectedPopChart();
    if (!chart) return;
    chart.data.labels = data.labels || [];
    chart.data.datasets[0].data = data.event_counts || [];
    chart.data.datasets[1].data = data.affected_counts || [];
    chart.update();
    const sum = (data.event_counts || []).reduce((a, v) => a + Number(v || 0), 0);
    setEmptyState("rumorOverviewAffectedPopEmpty", sum === 0);
  };

  // ── Map ────────────────────────────────────────────────────────────────────
  const syncRumorMapSectionHeight = () => {
    const el = getEl("rumorOverviewMapSection");
    if (el) el.style.removeProperty("height");
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
    }
  };

  const refreshDataOnly = async () => {
    const filters = getFilters();
    syncFilterHiddenFields();
    syncUrl(filters);
    const [summary, trend, sourceByProvince, affectedPop] = await Promise.all([
      fetchJSON(endpoints.summary, filters),
      fetchJSON(endpoints.trend, filters),
      fetchJSON(endpoints.sourceByProvince, filters),
      fetchJSON(endpoints.affectedPop, filters),
    ]);
    renderSummary(summary);
    renderTrend(trend);
    renderSourceByProvince(sourceByProvince);
    renderAffectedPop(affectedPop);
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
      filterElements.form.addEventListener("submit", (e) => { e.preventDefault(); refreshAll().catch(console.error); });
    }
    for (const el of [filterElements.preset, filterElements.start, filterElements.end]) {
      if (el) el.addEventListener("change", () => { if (!shouldApplyDateRangeChange()) return; refreshAll().catch(console.error); });
    }
    if (filterElements.mapViewSelect) {
      filterElements.mapViewSelect.addEventListener("change", () => refreshMapOnly().catch(console.error));
    }
    if (filterElements.reset) {
      filterElements.reset.addEventListener("click", () => {
        if (filterElements.preset) filterElements.preset.value = "all_time";
        if (filterElements.start) filterElements.start.value = "";
        if (filterElements.end) filterElements.end.value = "";
        if (filterElements.mapViewSelect) filterElements.mapViewSelect.value = "Province";
        refreshAll().catch(console.error);
      });
    }
  };

  // ── Init ───────────────────────────────────────────────────────────────────
  const resizeVisuals = () => {
    syncRumorMapSectionHeight();
    if (chartState.trend) chartState.trend.resize();
    if (chartState.sourceByProvince) chartState.sourceByProvince.resize();
    if (chartState.affectedPop) chartState.affectedPop.resize();
    if (mapController) mapController.resize();
  };

  const init = async () => {
    setupDateInputs([filterElements.start, filterElements.end]);
    bindEvents();
    await refreshAll();
    requestAnimationFrame(() => { syncRumorMapSectionHeight(); if (mapController) mapController.resize(); });
  };

  const pane = app.closest(".tab-pane");
  if (pane && !pane.classList.contains("show")) {
    let initializedFromTab = false;
    const isPaneActivationEvent = (event) => {
      if (!event) return false;
      if (event.type === "dashboard:refresh-tab") {
        const { shell = "", tab = "" } = event.detail || {};
        const paneTab = pane.dataset.tabPanel || "";
        const paneShell = app.closest(".dashboard-shell")?.dataset?.shell || "";
        if (shell && paneShell && shell !== paneShell) return false;
        return !!paneTab && tab === paneTab;
      }
      const sel = event.target?.getAttribute("data-bs-target") || event.target?.getAttribute("data-target");
      return sel === `#${pane.id}`;
    };
    const activateHandler = (event) => {
      if (!isPaneActivationEvent(event)) return;
      if (!initializedFromTab) {
        initializedFromTab = true;
        init().then(() => setTimeout(resizeVisuals, 0)).catch(console.error);
      } else {
        setTimeout(resizeVisuals, 0);
      }
    };
    if (window.jQuery) window.jQuery(document).on("shown.bs.tab", activateHandler);
    else document.addEventListener("shown.bs.tab", activateHandler);
    document.addEventListener("dashboard:refresh-tab", activateHandler);
    return;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => init().then(() => setTimeout(resizeVisuals, 0)).catch(console.error));
  } else {
    init().then(() => setTimeout(resizeVisuals, 0)).catch(console.error);
  }
})();
