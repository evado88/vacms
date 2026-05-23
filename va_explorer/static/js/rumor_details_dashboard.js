(function () {
  const app = document.getElementById("rumorDetailsDashboardApp");
  if (!app) return;

  const endpoints = {
    enumeratorStats: app.dataset.enumeratorStatsEndpoint,
    descriptionTable: app.dataset.descriptionTableEndpoint,
    wordCloud: app.dataset.wordCloudEndpoint,
    sentiment: app.dataset.sentimentEndpoint,
  };

  let tableState = { page: 1, totalPages: 1, filters: null };
  let sentimentChart = null;

  const getEl = (id) => document.getElementById(id);
  const getFirstEl = (...ids) => ids.map(getEl).find(Boolean) || null;

  const filterElements = {
    form: getFirstEl("rumorDetailsFiltersForm", "rumorOverviewFiltersForm"),
    preset: getFirstEl("rumorDetailsFilterTimePreset", "rumorOverviewFilterTimePreset"),
    start: getFirstEl("rumorDetailsFilterStartDatetime", "rumorOverviewFilterStartDatetime"),
    end: getFirstEl("rumorDetailsFilterEndDatetime", "rumorOverviewFilterEndDatetime"),
    geoLevel: getEl("rumorDetailsFilterGeoLevel"),
    geoValue: getEl("rumorDetailsFilterGeoValue"),
    reset: getFirstEl("rumorDetailsFiltersReset", "rumorOverviewFiltersReset"),
  };

  const getFilters = () => ({
    time_preset: filterElements.preset?.value || "all_time",
    start_datetime: filterElements.start?.value || "",
    end_datetime: filterElements.end?.value || "",
    geography_level: filterElements.geoLevel?.value || "",
    geography_value: (filterElements.geoValue?.value || "").trim(),
  });

  const buildParams = (filters) => {
    const params = new URLSearchParams();
    if (filters.time_preset && filters.time_preset !== "all_time") params.set("time_preset", filters.time_preset);
    if (filters.start_datetime) params.set("start_datetime", filters.start_datetime);
    if (filters.end_datetime) params.set("end_datetime", filters.end_datetime);
    if (filters.geography_level && filters.geography_value) {
      params.set("geography_level", filters.geography_level);
      params.set("geography_value", filters.geography_value);
    }
    return params;
  };

  const syncUrl = (filters) => {
    const params = buildParams(filters);
    const currentTab = new URLSearchParams(window.location.search).get("tab");
    if (currentTab) params.set("tab", currentTab);
    const rumorView = new URLSearchParams(window.location.search).get("rumor_view");
    if (rumorView) params.set("rumor_view", rumorView);
    const query = params.toString();
    window.history.replaceState({}, "", query ? `${window.location.pathname}?${query}` : window.location.pathname);
  };

  const fetchJSON = async (url, filters, extra) => {
    const params = buildParams(filters);
    if (extra) Object.entries(extra).forEach(([k, v]) => params.set(k, v));
    const requestUrl = params.toString() ? `${url}?${params.toString()}` : url;
    const response = await fetch(requestUrl, {
      method: "GET",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error(`Request failed: ${requestUrl}`);
    return response.json();
  };

  const setEmptyState = (id, isEmpty) => { const el = getEl(id); if (el) el.hidden = !isEmpty; };
  const escapeHtml = (value) => String(value || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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

  // ── Enumerator leaderboard table ───────────────────────────────────────────
  const renderEnumeratorStats = (data) => {
    const tbody = getEl("rumorDetailsEnumeratorTableBody");
    if (!tbody) return;
    const rows = data.rows || [];
    setEmptyState("rumorDetailsEnumeratorEmpty", rows.length === 0);
    if (!rows.length) { tbody.innerHTML = ""; return; }
    const maxCount = rows.reduce((m, r) => Math.max(m, r.count), 1);
    tbody.innerHTML = rows.map((r) => {
      const barW = Math.round((r.count / maxCount) * 100);
      const district = escapeHtml(r.district || "");
      const constituency = escapeHtml(r.constituency || "");
      const ward = escapeHtml(r.ward || "");
      const date = r.last_reported ? r.last_reported.slice(0, 10) : "—";
      return `<tr>
        <td class="rumor-lb-location" title="${district}">${district}</td>
        <td class="rumor-lb-location" title="${constituency}">${constituency}</td>
        <td class="rumor-lb-location" title="${ward}">${ward}</td>
        <td class="rumor-lb-count">
          <div class="rumor-lb-bar-wrap"><div class="rumor-lb-bar" style="width:${barW}%"></div></div>
          <span>${r.count}</span>
        </td>
        <td class="rumor-lb-date">${date}</td>
      </tr>`;
    }).join("");
  };

  // ── Description table ─────────────────────────────────────────────────────
  const CATEGORY_COLORS = {
    "Vaccine / Immunization": "#dbeafe",
    "Disease Outbreak": "#fee2e2",
    "Death / Mortality": "#f3e8ff",
    "Family / Household": "#fce7f3",
    "Water / Sanitation": "#e0f2fe",
    "Food / Nutrition": "#fef9c3",
    "Health Facility": "#d1fae5",
    "General Concern": "#f3f4f6",
  };

  const renderDescriptionTable = (data) => {
    const tbody = getEl("rumorDetailsDescTableBody");
    const pagination = getEl("rumorDetailsDescTablePagination");
    const pageInfo = getEl("rumorDetailsDescTablePageInfo");
    if (!tbody) return;

    const rows = data.rows || [];
    const totalRows = data.total_rows || 0;
    const page = data.page || 1;
    const totalPages = data.total_pages || 1;

    tableState.page = page;
    tableState.totalPages = totalPages;

    setEmptyState("rumorDetailsDescTableEmpty", totalRows === 0);
    if (pagination) pagination.hidden = totalPages <= 1;

    if (!rows.length) { tbody.innerHTML = ""; return; }

    tbody.innerHTML = rows.map((r) => {
      const catColor = CATEGORY_COLORS[r.category] || "#f3f4f6";
      const desc = escapeHtml(r.description);
      const truncated = desc.length > 120 ? desc.slice(0, 120) + "…" : desc;
      const pct = Number(r.pct || 0).toFixed(1);
      return `<tr>
        <td class="rumor-dt-desc" title="${desc}">${truncated}</td>
        <td class="rumor-dt-cat"><span class="rumor-dt-badge" style="background:${catColor}">${r.category}</span></td>
        <td class="rumor-dt-count">${r.count}</td>
        <td class="rumor-dt-pct">${pct}%</td>
        <td class="rumor-dt-source">${r.source || "—"}</td>
      </tr>`;
    }).join("");

    if (pageInfo) pageInfo.textContent = `Page ${page} of ${totalPages} (${totalRows} total)`;
    const prevBtn = getEl("rumorDetailsDescTablePrev");
    const nextBtn = getEl("rumorDetailsDescTableNext");
    if (prevBtn) prevBtn.disabled = page <= 1;
    if (nextBtn) nextBtn.disabled = page >= totalPages;
  };

  const fetchTablePage = async (page) => {
    if (!tableState.filters) return;
    const data = await fetchJSON(endpoints.descriptionTable, tableState.filters, { page });
    renderDescriptionTable(data);
  };

  // ── Sentiment scatter plot ────────────────────────────────────────────────
  const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  const monthToIndex = (monthStr) => {
    if (!monthStr || typeof monthStr !== "string") return null;
    const [y, m] = monthStr.split("-").map(Number);
    if (!Number.isFinite(y) || !Number.isFinite(m)) return null;
    return y * 12 + (m - 1);
  };

  const indexToMonthLabel = (idx) => {
    const i = Math.round(idx);
    const year = Math.floor(i / 12);
    const month = i - year * 12;
    if (month < 0 || month > 11) return "";
    return `${MONTH_LABELS[month]} ${year}`;
  };

  // Stable jitter in [-0.4, +0.4] (months) so overlapping points stay visible.
  const jitterFor = (id) => {
    const seed = Number.isFinite(id) ? id : 0;
    const v = Math.sin(seed * 9301 + 49297) * 0.5;
    return Math.max(-0.4, Math.min(0.4, v));
  };

  const sizeForRuNum = (ruNum) => {
    const n = Math.max(0, Number(ruNum) || 0);
    const r = 4 + Math.sqrt(n) * 1.5;
    return Math.max(4, Math.min(28, r));
  };

  // Inline plugin: draw a horizontal reference line at y = 0.
  const ZeroLinePlugin = {
    id: "rdsZeroLine",
    afterDraw(chart) {
      const yScale = chart.scales.y;
      const xScale = chart.scales.x;
      if (!yScale || !xScale) return;
      const y = yScale.getPixelForValue(0);
      const { ctx } = chart;
      ctx.save();
      ctx.beginPath();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = "#9ca3af";
      ctx.lineWidth = 1;
      ctx.moveTo(xScale.left, y);
      ctx.lineTo(xScale.right, y);
      ctx.stroke();
      ctx.restore();
    },
  };

  const buildSentimentDatasets = (points) => {
    const positive = [];
    const neutral = [];
    const negative = [];
    // Sort largest bubbles first so smaller ones layer on top.
    const sorted = [...(points || [])].sort((a, b) => (b.ru_num || 0) - (a.ru_num || 0));
    for (const p of sorted) {
      const idx = monthToIndex(p.month);
      if (idx == null) continue;
      const x = idx + jitterFor(p.id);
      const y = Number(p.weighted) || 0;
      const point = {
        x,
        y,
        r: sizeForRuNum(p.ru_num),
        meta: p,
      };
      if (y > 0) positive.push(point);
      else if (y < 0) negative.push(point);
      else neutral.push(point);
    }
    return { positive, neutral, negative };
  };

  const renderSentimentCounts = (counts) => {
    const el = getEl("rumorDetailsSentimentCounts");
    if (!el) return;
    const c = counts || {};
    const total = (c.positive || 0) + (c.neutral || 0) + (c.negative || 0);
    if (total === 0) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.innerHTML = `
      <span class="rds-counts-pos">${c.positive || 0} positive</span>
      <span class="rds-counts-sep">·</span>
      <span class="rds-counts-neu">${c.neutral || 0} neutral <span class="rds-counts-note">(incl. SRS programme context)</span></span>
      <span class="rds-counts-sep">·</span>
      <span class="rds-counts-neg">${c.negative || 0} negative</span>
    `;
  };

  const renderSentimentScatter = (data) => {
    const canvas = getEl("rumorDetailsSentimentChart");
    if (!canvas || typeof Chart === "undefined") return;

    const points = (data && data.points) || [];
    const counts = (data && data.counts) || { positive: 0, neutral: 0, negative: 0 };
    const range = (data && data.range) || {};

    setEmptyState("rumorDetailsSentimentEmpty", points.length === 0);
    renderSentimentCounts(counts);

    if (sentimentChart) sentimentChart.destroy();

    if (!points.length) {
      sentimentChart = null;
      return;
    }

    const { positive, neutral, negative } = buildSentimentDatasets(points);
    const allX = positive.concat(neutral, negative).map((p) => p.x);
    const rangeStart = monthToIndex(range.start_month);
    const rangeEnd = monthToIndex(range.end_month);
    const minX = (rangeStart != null) ? rangeStart - 0.5 : Math.min(...allX) - 0.5;
    const maxX = (rangeEnd != null) ? rangeEnd + 0.5 : Math.max(...allX) + 0.5;

    sentimentChart = new Chart(canvas.getContext("2d"), {
      type: "bubble",
      data: {
        datasets: [
          {
            label: "Positive",
            data: positive,
            backgroundColor: "rgba(34, 197, 94, 0.55)",
            borderColor: "rgba(22, 163, 74, 0.9)",
            borderWidth: 1,
          },
          {
            label: "Neutral",
            data: neutral,
            backgroundColor: "rgba(148, 163, 184, 0.55)",
            borderColor: "rgba(100, 116, 139, 0.9)",
            borderWidth: 1,
          },
          {
            label: "Negative",
            data: negative,
            backgroundColor: "rgba(239, 68, 68, 0.55)",
            borderColor: "rgba(220, 38, 38, 0.9)",
            borderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { position: "top", labels: { boxWidth: 12 } },
          tooltip: {
            callbacks: {
              title: (items) => {
                const it = items?.[0];
                if (!it) return "";
                const m = it.raw?.meta;
                return m ? `${m.date} · ${indexToMonthLabel(monthToIndex(m.month))}` : "";
              },
              label: (item) => {
                const m = item.raw?.meta;
                if (!m) return "";
                const desc = m.description.length > 140 ? `${m.description.slice(0, 140)}…` : m.description;
                const loc = [m.province, m.district].filter(Boolean).join(" / ") || "—";
                const lines = [
                  desc,
                  `Source: ${m.source || "Unknown"}`,
                  `Location: ${loc}`,
                  `Affected: ${m.ru_num}`,
                  `Sentiment: ${m.sentiment.toFixed(2)} · weighted: ${m.weighted.toFixed(2)}`,
                ];
                if (m.category) lines.push(`Category: ${m.category}`);
                if (m.is_srs_domain) lines.push("SRS programme context");
                return lines;
              },
            },
          },
        },
        scales: {
          x: {
            type: "linear",
            min: minX,
            max: maxX,
            title: { display: true, text: "Month" },
            ticks: {
              stepSize: 1,
              callback: (v) => indexToMonthLabel(v),
              maxRotation: 0,
              autoSkipPadding: 12,
            },
            grid: { color: "rgba(0,0,0,0.04)" },
          },
          y: {
            title: { display: true, text: "Weighted sentiment (sentiment × log(1 + affected))" },
            grid: { color: "rgba(0,0,0,0.04)" },
          },
        },
      },
      plugins: [ZeroLinePlugin],
    });
  };

  // ── Refresh helpers ────────────────────────────────────────────────────────
  const refreshAll = async () => {
    const filters = getFilters();
    tableState.filters = filters;
    tableState.page = 1;
    syncUrl(filters);

    const requests = [
      fetchJSON(endpoints.enumeratorStats, filters),
      fetchJSON(endpoints.descriptionTable, filters, { page: 1 }),
    ];
    if (endpoints.sentiment) requests.push(fetchJSON(endpoints.sentiment, filters));

    const responses = await Promise.all(requests);
    const enumeratorStats = responses[0];
    const tableData = responses[1];
    const sentimentData = endpoints.sentiment ? responses[2] : null;

    renderEnumeratorStats(enumeratorStats);
    renderDescriptionTable(tableData);
    if (endpoints.sentiment) renderSentimentScatter(sentimentData);

    if (window.RumorWordCloud && endpoints.wordCloud) {
      window.RumorWordCloud.refresh(endpoints.wordCloud, filters, fetchJSON).catch(console.error);
    }
  };

  // ── Event bindings ─────────────────────────────────────────────────────────
  const bindEvents = () => {
    if (filterElements.form) {
      filterElements.form.addEventListener("submit", (e) => { e.preventDefault(); refreshAll().catch(console.error); });
    }
    for (const el of [filterElements.preset, filterElements.start, filterElements.end, filterElements.geoLevel]) {
      if (el) el.addEventListener("change", () => { if (!shouldApplyDateRangeChange()) return; refreshAll().catch(console.error); });
    }
    if (filterElements.geoValue) {
      let geoTimer;
      filterElements.geoValue.addEventListener("input", () => {
        clearTimeout(geoTimer);
        geoTimer = setTimeout(() => refreshAll().catch(console.error), 350);
      });
    }
    if (filterElements.reset) {
      filterElements.reset.addEventListener("click", () => {
        if (filterElements.preset) filterElements.preset.value = "all_time";
        if (filterElements.start) filterElements.start.value = "";
        if (filterElements.end) filterElements.end.value = "";
        if (filterElements.geoLevel) filterElements.geoLevel.value = "";
        if (filterElements.geoValue) filterElements.geoValue.value = "";
        refreshAll().catch(console.error);
      });
    }
    const prevBtn = getEl("rumorDetailsDescTablePrev");
    const nextBtn = getEl("rumorDetailsDescTableNext");
    if (prevBtn) prevBtn.addEventListener("click", () => {
      if (tableState.page > 1) fetchTablePage(tableState.page - 1).catch(console.error);
    });
    if (nextBtn) nextBtn.addEventListener("click", () => {
      if (tableState.page < tableState.totalPages) fetchTablePage(tableState.page + 1).catch(console.error);
    });
  };

  // ── Resize helper (called on tab re-activation after first init) ──────────
  const resizeVisuals = () => {
    if (window.RumorWordCloud) window.RumorWordCloud.resize();
    if (sentimentChart) sentimentChart.resize();
  };

  document.addEventListener("rumor-surveillance:subtab-change", (event) => {
    if ((event.detail || {}).tab === "details") {
      setTimeout(resizeVisuals, 0);
    }
  });

  // ── Init ───────────────────────────────────────────────────────────────────
  const init = async () => {
    setupDateInputs([filterElements.start, filterElements.end]);
    if (window.RumorWordCloud) window.RumorWordCloud.init();
    bindEvents();
    await refreshAll();
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
        init().catch(console.error);
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
    document.addEventListener("DOMContentLoaded", () => init().catch(console.error));
  } else {
    init().catch(console.error);
  }
})();
