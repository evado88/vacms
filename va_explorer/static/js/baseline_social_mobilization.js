// Social Mobilization tab controller for the Baseline Census Dashboard.
//
// Mirrors the Census Chart + Census Table tabs: a JSON endpoint drives Chart.js
// sub-tabs (Reach / Refusals / Rumours / Trend) with province → district →
// health facility → village drill-down, and an HTML component (KPIs + an
// aggregated table + a rumour log) is re-fetched on sort/search/filter changes.
//
// Shared filter/format/chart helpers are reused from baseline_census_dashboard.js
// via window.BaselineCensusShared so both tabs share one stack.
(function () {
  "use strict";

  const shared = window.BaselineCensusShared || {};
  const formatCount =
    shared.formatCountValue ||
    ((v) => (v === null || v === undefined ? "" : Number(v).toLocaleString()));
  const formatPct =
    shared.formatCoverageValue ||
    ((v) => (v === null || v === undefined ? "" : `${v}%`));
  const appendFilterParams =
    shared.appendBaselineFilterParams || ((params) => params);
  const labelsPlugin = shared.enumerationCoverageLabelsPlugin || null;

  // Per-canvas chart registry (reuse the census WeakMap when available so the
  // viewport resizer in the census file can also see our charts).
  const chartInstances = shared.chartInstances || new WeakMap();
  const destroyChartForCanvas =
    shared.destroyChartForCanvas ||
    ((canvas) => {
      const existing = chartInstances.get(canvas);
      if (existing) {
        existing.destroy();
        chartInstances.delete(canvas);
      }
    });

  const LEVEL_LABELS = {
    province: { singular: "province", children: "districts" },
    district: { singular: "district", children: "health facilities" },
    health_facility: { singular: "health facility", children: "villages" },
    village_neighbourhood: { singular: "village", children: null },
  };

  // Palette used for the multicolor people-reached chart (one colour per bar).
  const CHART_PALETTE = [
    "#2563eb", "#16a34a", "#dc2626", "#d97706", "#7c3aed",
    "#0891b2", "#be185d", "#15803d", "#b45309", "#1d4ed8",
    "#ea580c", "#0e7490", "#7e22ce", "#166534", "#9f1239",
    "#0369a1", "#92400e", "#065f46", "#1e1b4b", "#4a044e",
  ];

  // Sub-tabs. "results" sub-tabs chart per-location grouped bars (drill on
  // click); "multicolor" charts a single series with one colour per bar;
  // "trend" charts the daily time series and does not drill.
  const SUBTABS = [
    {
      key: "event_types",
      canvasId: "smEventTypesChart",
      kind: "results",
      series: [
        { key: "households_count", label: "Households", color: "#2563eb" },
        { key: "schools_count", label: "Schools", color: "#7c3aed" },
        { key: "religious_org_count", label: "Religious orgs", color: "#0891b2" },
        { key: "other_count", label: "Other", color: "#94a3b8" },
      ],
    },
    {
      key: "people_reached",
      canvasId: "smPeopleReachedChart",
      kind: "multicolor",
      seriesKey: "people_reached",
      seriesLabel: "People reached",
    },
    {
      key: "refusals",
      canvasId: "smRefusalsChart",
      kind: "results",
      bars: [
        { key: "refusal_reluctance_identified", label: "Identified", color: "#dc2626" },
        { key: "refusal_reluctance_managed", label: "Managed", color: "#16a34a" },
      ],
      rate: { key: "refusal_managed_rate", label: "Managed rate" },
    },
    {
      key: "rumours",
      canvasId: "smRumoursChart",
      kind: "results",
      series: [
        { key: "rumours_reported", label: "Rumours reported", color: "#7c3aed" },
      ],
    },
    {
      key: "trend",
      canvasId: "smTrendChart",
      kind: "trend",
    },
  ];
  const subtabByKey = (key) => SUBTABS.find((s) => s.key === key) || SUBTABS[0];

  let initialized = false;
  let activeSubtab = "event_types";
  let fetchRequestId = 0;
  let lastPayload = null;
  // Drill-down position: the level currently shown plus accumulated parents.
  let chartNav = { level: "province", parents: {} };

  // Table state mirrors the server-rendered component's sort/search/pagination.
  const tableDefaults = { sm_sort: "province", sm_dir: "asc", sm_q: "", sm_page: "1" };
  let tableState = Object.assign({}, tableDefaults);
  let searchTimer = null;

  const byId = (id) => document.getElementById(id);
  const getChartUrl = () => {
    const root = byId("sm-chart-view");
    return root ? root.dataset.endpoint || "" : "";
  };
  const getComponentUrl = () => {
    const slot = byId("baselineSocialMobSlot");
    return slot ? slot.dataset.endpoint || "" : "";
  };
  const getKpisUrl = () => {
    const slot = byId("baselineSocialMobKpisSlot");
    return slot ? slot.dataset.endpoint || "" : "";
  };

  const syncKpiRailHeight = () => {
    const root = byId("sm-view");
    const cards = root?.querySelector("#baselineSocialMobKpisSlot .nov-kpi-cards");
    if (!root || !cards) return;
    const height = Math.ceil(cards.getBoundingClientRect().height);
    if (height > 0) {
      root.style.setProperty("--sm-kpi-rail-height", `${height}px`);
    }
  };

  // KPI cards live above the sub-tabs and stay visible on every view, so they
  // are refreshed independently of the tables.
  const refreshKpis = () => {
    const slot = byId("baselineSocialMobKpisSlot");
    const url = getKpisUrl();
    if (!slot || !url) return Promise.resolve();
    const params = new URLSearchParams();
    appendFilterParams(params);
    return fetch(`${url}?${params.toString()}`, {
      method: "GET",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Social mobilization KPIs fetch failed: ${res.status}`);
        return res.text();
      })
      .then((html) => {
        slot.innerHTML = html;
        window.requestAnimationFrame(syncKpiRailHeight);
      })
      .catch((err) => console.error("[social-mob] KPI refresh failed", err));
  };

  // ----- overlays --------------------------------------------------------
  const setOverlay = (state) => {
    const overlays = {
      loading: "smChartLoading",
      empty: "smChartEmpty",
      error: "smChartError",
    };
    Object.values(overlays).forEach((id) => {
      const el = byId(id);
      if (el) el.hidden = true;
    });
    const activeId = overlays[state];
    if (activeId) {
      const el = byId(activeId);
      if (el) el.hidden = false;
    }
  };

  // ----- breadcrumb + drill-down ----------------------------------------
  const navigateTo = (crumb) => {
    if (!crumb) return;
    chartNav = { level: crumb.level, parents: { ...(crumb.parents || {}) } };
    fetchChartData();
  };

  const drillInto = (row) => {
    if (!row || !lastPayload) return;
    const childLevel = lastPayload.child_level;
    if (!childLevel || !row.has_children) return;
    chartNav = {
      level: childLevel,
      parents: { ...chartNav.parents, [row.level]: row.value },
    };
    fetchChartData();
  };

  const renderBreadcrumb = (payload) => {
    const nav = byId("smChartBreadcrumb");
    const backBtn = byId("smChartBack");
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
      if (index === crumbs.length - 1) {
        const current = document.createElement("span");
        current.className = "baseline-chart-breadcrumb-current";
        current.textContent = crumb.label;
        nav.appendChild(current);
      } else {
        const link = document.createElement("button");
        link.type = "button";
        link.className = "baseline-chart-breadcrumb-link";
        link.textContent = crumb.label;
        link.addEventListener("click", () => navigateTo(crumb));
        nav.appendChild(link);
      }
    });
    if (backBtn) backBtn.hidden = crumbs.length <= 1;
  };

  const renderHint = (payload) => {
    const hint = byId("smChartHint");
    if (!hint) return;
    if (activeSubtab === "trend") {
      hint.textContent = "Daily totals for the current selection.";
      return;
    }
    const level = (payload && payload.level) || "province";
    const meta = LEVEL_LABELS[level] || LEVEL_LABELS.province;
    if (payload && payload.child_level && meta.children) {
      hint.textContent =
        `Tip: click a ${meta.singular} bar (or its label) to drill down ` +
        `into its ${meta.children}.`;
    } else {
      hint.textContent =
        "Village is the deepest level — no further drill-down available.";
    }
  };

  // ----- chart rendering -------------------------------------------------
  const sizeCanvasBox = (box, count) => {
    if (!box) return;
    const wrap = box.closest(".baseline-chart-wrap");
    if (!wrap) return;
    const needed = count * 64;
    box.style.width = needed > wrap.clientWidth ? `${needed}px` : "100%";
  };

  const buildResultsDatasets = (subtab, results) => {
    const datasets = [];
    if (subtab.bars) {
      subtab.bars.forEach((bar) => {
        datasets.push({
          type: "bar",
          label: bar.label,
          data: results.map((row) => row[bar.key]),
          backgroundColor: bar.color,
          borderRadius: 4,
          yAxisID: "y",
        });
      });
    }
    if (subtab.series) {
      subtab.series.forEach((series) => {
        datasets.push({
          type: "bar",
          label: series.label,
          data: results.map((row) => row[series.key]),
          backgroundColor: series.color,
          borderRadius: 4,
          yAxisID: "y",
        });
      });
    }
    if (subtab.rate) {
      datasets.push({
        type: "line",
        label: subtab.rate.label,
        data: results.map((row) => row[subtab.rate.key]),
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
        yAxisID: "yRate",
      });
    }
    return datasets;
  };

  const renderResultsChart = (subtab) => {
    const canvas = byId(subtab.canvasId);
    if (!canvas || typeof Chart === "undefined" || !lastPayload) return;
    destroyChartForCanvas(canvas);

    const results = lastPayload.results || [];
    const labels = results.map((row) => row.label || "—");
    const datasets = buildResultsDatasets(subtab, results);

    const scales = {
      x: {
        grid: { display: false },
        ticks: { autoSkip: false, maxRotation: 45, minRotation: 0 },
      },
      y: {
        beginAtZero: true,
        position: "left",
        title: { display: true, text: "Counts" },
        ticks: { callback: (value) => formatCount(value) },
      },
    };
    if (subtab.rate) {
      scales.yRate = {
        beginAtZero: true,
        suggestedMax: 100,
        position: "right",
        title: { display: true, text: "Managed rate" },
        grid: { drawOnChartArea: false },
        ticks: { callback: (value) => formatPct(value) },
      };
    }

    sizeCanvasBox(canvas.closest(".baseline-chart-canvas-box"), results.length);

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
          drillInto(results[elements[0].index]);
        },
        onHover: (evt, elements) => {
          const target = evt && evt.native && evt.native.target;
          if (!target) return;
          const row = elements && elements.length ? results[elements[0].index] : null;
          target.style.cursor = row && row.has_children ? "pointer" : "default";
        },
        plugins: {
          legend: { display: true, position: "top", labels: { boxWidth: 12, boxHeight: 12 } },
          tooltip: {
            callbacks: {
              title(items) {
                if (!items.length) return "";
                const row = results[items[0].dataIndex] || {};
                return row.label || "—";
              },
              label(context) {
                const value = context.raw;
                if (value === null || value === undefined) return "";
                const label = context.dataset.label || "";
                const formatted =
                  context.dataset.yAxisID === "yRate"
                    ? formatPct(value)
                    : formatCount(value);
                return `${label}: ${formatted}`;
              },
            },
          },
        },
        scales,
      },
      plugins: labelsPlugin ? [labelsPlugin] : [],
    });
    chartInstances.set(canvas, chart);
  };

  const renderTrendChart = () => {
    const subtab = subtabByKey("trend");
    const canvas = byId(subtab.canvasId);
    if (!canvas || typeof Chart === "undefined" || !lastPayload) return;
    destroyChartForCanvas(canvas);

    const trend = lastPayload.trend || [];
    const labels = trend.map((row) => row.date);
    const datasets = [
      {
        label: "People reached",
        data: trend.map((row) => row.people_reached),
        borderColor: "#16a34a",
        backgroundColor: "#16a34a",
        borderWidth: 2,
        pointRadius: 3,
        tension: 0.25,
      },
      {
        label: "Households",
        data: trend.map((row) => row.households_count),
        borderColor: "#2563eb",
        backgroundColor: "#2563eb",
        borderWidth: 2,
        pointRadius: 3,
        tension: 0.25,
      },
      {
        label: "Schools",
        data: trend.map((row) => row.schools_count),
        borderColor: "#7c3aed",
        backgroundColor: "#7c3aed",
        borderWidth: 2,
        pointRadius: 3,
        tension: 0.25,
      },
      {
        label: "Religious orgs",
        data: trend.map((row) => row.religious_org_count),
        borderColor: "#0891b2",
        backgroundColor: "#0891b2",
        borderWidth: 2,
        pointRadius: 3,
        tension: 0.25,
      },
      {
        label: "Other",
        data: trend.map((row) => row.other_count),
        borderColor: "#94a3b8",
        backgroundColor: "#94a3b8",
        borderWidth: 2,
        pointRadius: 3,
        tension: 0.25,
      },
    ];

    sizeCanvasBox(canvas.closest(".baseline-chart-canvas-box"), trend.length);

    const chart = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        plugins: {
          legend: { display: true, position: "top", labels: { boxWidth: 12, boxHeight: 12 } },
          tooltip: {
            callbacks: {
              label(context) {
                const value = context.raw;
                if (value === null || value === undefined) return "";
                return `${context.dataset.label || ""}: ${formatCount(value)}`;
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false } },
          y: {
            beginAtZero: true,
            title: { display: true, text: "Counts" },
            ticks: { callback: (value) => formatCount(value) },
          },
        },
      },
    });
    chartInstances.set(canvas, chart);
  };

  const renderMulticolorChart = (subtab) => {
    const canvas = byId(subtab.canvasId);
    if (!canvas || typeof Chart === "undefined" || !lastPayload) return;
    destroyChartForCanvas(canvas);

    const results = lastPayload.results || [];
    const labels = results.map((row) => row.label || "—");
    const colors = results.map((_, i) => CHART_PALETTE[i % CHART_PALETTE.length]);

    sizeCanvasBox(canvas.closest(".baseline-chart-canvas-box"), results.length);

    const chart = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: subtab.seriesLabel,
            data: results.map((row) => row[subtab.seriesKey]),
            backgroundColor: colors,
            borderRadius: 4,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        layout: { padding: { top: 22 } },
        onClick: (evt, elements) => {
          if (!elements || !elements.length) return;
          drillInto(results[elements[0].index]);
        },
        onHover: (evt, elements) => {
          const target = evt && evt.native && evt.native.target;
          if (!target) return;
          const row = elements && elements.length ? results[elements[0].index] : null;
          target.style.cursor = row && row.has_children ? "pointer" : "default";
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title(items) {
                return items.length ? (results[items[0].dataIndex] || {}).label || "—" : "";
              },
              label(context) {
                const value = context.raw;
                if (value === null || value === undefined) return "";
                return `${subtab.seriesLabel}: ${formatCount(value)}`;
              },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { autoSkip: false, maxRotation: 45, minRotation: 0 },
          },
          y: {
            beginAtZero: true,
            title: { display: true, text: "People reached" },
            ticks: { callback: (value) => formatCount(value) },
          },
        },
      },
      plugins: labelsPlugin ? [labelsPlugin] : [],
    });
    chartInstances.set(canvas, chart);
  };

  const renderActiveSubtab = () => {
    if (!lastPayload) return;
    const subtab = subtabByKey(activeSubtab);
    if (subtab.kind === "trend") {
      renderTrendChart();
      return;
    }
    if (!lastPayload.results || lastPayload.results.length === 0) return;
    if (subtab.kind === "multicolor") {
      renderMulticolorChart(subtab);
      return;
    }
    renderResultsChart(subtab);
  };

  const fetchChartData = () => {
    const url = getChartUrl();
    if (!url) return Promise.resolve();

    const requestId = ++fetchRequestId;
    setOverlay("loading");

    const params = new URLSearchParams();
    params.set("level", chartNav.level);
    Object.entries(chartNav.parents).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    appendFilterParams(params);

    return fetch(`${url}?${params.toString()}`, {
      method: "GET",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Social mobilization chart fetch failed: ${res.status}`);
        return res.json();
      })
      .then((payload) => {
        if (requestId !== fetchRequestId) return;
        if (payload.error) throw new Error(payload.error);
        lastPayload = payload;
        chartNav.level = payload.level || chartNav.level;
        renderBreadcrumb(payload);
        renderHint(payload);
        const hasResults = payload.results && payload.results.length > 0;
        const hasTrend = payload.trend && payload.trend.length > 0;
        if (!hasResults && !hasTrend) {
          SUBTABS.forEach((subtab) => destroyChartForCanvas(byId(subtab.canvasId)));
          setOverlay("empty");
          return;
        }
        setOverlay(null);
        renderActiveSubtab();
      })
      .catch((err) => {
        if (requestId !== fetchRequestId) return;
        console.error("[social-mob] chart fetch failed", err);
        setOverlay("error");
      });
  };

  // ----- sub-tab switching ----------------------------------------------
  let subtabsBound = false;
  const setActiveSubtab = (key) => {
    const subtab = subtabByKey(key);
    activeSubtab = subtab.key;
    document.querySelectorAll("#smChartTabs .census-chart-subtab").forEach((btn) => {
      const isActive = btn.dataset.smSubtab === subtab.key;
      btn.classList.toggle("census-chart-subtab--active", isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    document
      .querySelectorAll("#smChartSubpanels .census-chart-subpanel")
      .forEach((panel) => {
        panel.classList.toggle(
          "census-chart-subpanel--active",
          panel.dataset.subtab === subtab.key
        );
      });
    renderHint(lastPayload);
    renderActiveSubtab();
  };

  const bindSubtabs = () => {
    if (subtabsBound) return;
    subtabsBound = true;
    const tabs = byId("smChartTabs");
    if (!tabs) return;
    tabs.addEventListener("click", (event) => {
      const btn = event.target.closest(".census-chart-subtab");
      if (!btn || !btn.dataset.smSubtab) return;
      setActiveSubtab(btn.dataset.smSubtab);
    });
    const backBtn = byId("smChartBack");
    if (backBtn) {
      backBtn.addEventListener("click", () => {
        const crumbs = (lastPayload && lastPayload.breadcrumbs) || [];
        if (crumbs.length < 2) return;
        navigateTo(crumbs[crumbs.length - 2]);
      });
    }
  };

  // ----- component (KPIs + tables) --------------------------------------
  const refreshComponent = () => {
    const slot = byId("baselineSocialMobSlot");
    const url = getComponentUrl();
    if (!slot || !url) return Promise.resolve();

    const searchFocused = document.activeElement?.id === "smSearch";
    const params = new URLSearchParams();
    Object.entries(tableState).forEach(([key, val]) => {
      if (val) params.set(key, val);
    });
    appendFilterParams(params);

    return fetch(`${url}?${params.toString()}`, {
      method: "GET",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Social mobilization component fetch failed: ${res.status}`);
        return res.text();
      })
      .then((html) => {
        slot.innerHTML = html;
        slot.dataset.smLoaded = "1";
        setActiveTableTab(activeTableTab);
        if (searchFocused) {
          const input = byId("smSearch");
          if (input) {
            input.focus();
            const len = input.value.length;
            input.setSelectionRange(len, len);
          }
        }
      })
      .catch((err) => console.error("[social-mob] component refresh failed", err));
  };

  let tableEventsBound = false;
  const bindTableEvents = () => {
    if (tableEventsBound) return;
    tableEventsBound = true;

    document.addEventListener("input", (event) => {
      const target = event.target;
      if (!target || target.id !== "smSearch") return;
      tableState.sm_q = (target.value || "").trim();
      tableState.sm_page = "1";
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(refreshComponent, 800);
    });

    document.addEventListener("click", (event) => {
      const link = event.target.closest(".sm-sort-link, .sm-page-link");
      if (!link) return;
      if (
        link.classList.contains("disabled") ||
        link.getAttribute("aria-disabled") === "true"
      ) {
        return;
      }
      if (!link.closest("#baselineSocialMobSlot")) return;
      event.preventDefault();
      const params = new URLSearchParams((link.getAttribute("href") || "").replace("?", ""));
      if (link.classList.contains("sm-sort-link")) {
        tableState.sm_sort = params.get("sm_sort") || tableState.sm_sort;
        tableState.sm_dir = params.get("sm_dir") || tableState.sm_dir;
        tableState.sm_page = "1";
      } else {
        tableState.sm_page = params.get("sm_page") || tableState.sm_page;
      }
      refreshComponent();
    });
  };

  // ----- Activity / Rumour table switching ------------------------------
  let tableSubtabsBound = false;
  let activeTableTab = "activity";

  const setActiveTableTab = (key) => {
    activeTableTab = key === "rumour" ? "rumour" : "activity";
    document.querySelectorAll("#smTableTabs .sm-table-subtab").forEach((btn) => {
      const isActive = btn.dataset.smTableTab === activeTableTab;
      btn.classList.toggle("sm-table-subtab--active", isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    document.querySelectorAll("#baselineSocialMobSlot [data-sm-table-panel]").forEach((panel) => {
      const isActive = panel.dataset.smTablePanel === activeTableTab;
      panel.classList.toggle("sm-table-panel--active", isActive);
      panel.hidden = !isActive;
    });
  };

  const bindTableSubtabs = () => {
    if (tableSubtabsBound) return;
    tableSubtabsBound = true;
    const tabs = byId("smTableTabs");
    if (!tabs) return;
    tabs.addEventListener("click", (event) => {
      const btn = event.target.closest(".sm-table-subtab");
      if (!btn || !btn.dataset.smTableTab) return;
      setActiveTableTab(btn.dataset.smTableTab);
    });
  };

  // ----- Tables / Charts view switching ---------------------------------
  let viewSubtabsBound = false;
  let activeViewTab = "charts";
  let resizeBound = false;

  const setActiveViewTab = (key) => {
    activeViewTab = key === "charts" ? "charts" : "tables";
    document.querySelectorAll("#smViewTabs .sm-subtab").forEach((btn) => {
      const isActive = btn.dataset.smViewTab === activeViewTab;
      btn.classList.toggle("sm-subtab--active", isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    document.querySelectorAll("#sm-view .sm-view-panel").forEach((panel) => {
      panel.classList.toggle(
        "sm-view-panel--active",
        panel.dataset.smViewPanel === activeViewTab
      );
    });
    // Charts render into a previously-hidden panel, so (re)draw once visible
    // to pick up real canvas dimensions.
    if (activeViewTab === "charts") renderActiveSubtab();
  };

  const bindViewSubtabs = () => {
    if (viewSubtabsBound) return;
    viewSubtabsBound = true;
    const tabs = byId("smViewTabs");
    if (!tabs) return;
    tabs.addEventListener("click", (event) => {
      const btn = event.target.closest(".sm-subtab");
      if (!btn || !btn.dataset.smViewTab) return;
      setActiveViewTab(btn.dataset.smViewTab);
    });
  };

  // ----- public API ------------------------------------------------------
  const initTab = async () => {
    bindViewSubtabs();
    bindTableSubtabs();
    bindSubtabs();
    bindTableEvents();
    if (!resizeBound) {
      resizeBound = true;
      window.addEventListener("resize", syncKpiRailHeight);
    }
    if (initialized) {
      setActiveSubtab(activeSubtab);
      syncKpiRailHeight();
      return;
    }
    initialized = true;
    await Promise.all([refreshKpis(), refreshComponent(), fetchChartData()]);
  };

  const onFilterChange = () => {
    if (!initialized) return;
    // A filter change resets drill-down and table paging, like the census tabs.
    chartNav = { level: "province", parents: {} };
    tableState = Object.assign({}, tableDefaults);
    refreshKpis();
    refreshComponent();
    fetchChartData();
  };

  window.BaselineSocialMob = { initTab, onFilterChange };
})();
