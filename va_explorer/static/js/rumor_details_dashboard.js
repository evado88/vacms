(function () {
  const app = document.getElementById("rumorDetailsDashboardApp");
  if (!app) return;

  const endpoints = {
    enumeratorStats: app.dataset.enumeratorStatsEndpoint,
    descriptionTable: app.dataset.descriptionTableEndpoint,
  };

  let tableState = { page: 1, totalPages: 1, filters: null };

  const getEl = (id) => document.getElementById(id);

  const filterElements = {
    form: getEl("rumorDetailsFiltersForm"),
    preset: getEl("rumorDetailsFilterTimePreset"),
    start: getEl("rumorDetailsFilterStartDatetime"),
    end: getEl("rumorDetailsFilterEndDatetime"),
    reset: getEl("rumorDetailsFiltersReset"),
  };

  const getFilters = () => ({
    time_preset: filterElements.preset?.value || "all_time",
    start_datetime: filterElements.start?.value || "",
    end_datetime: filterElements.end?.value || "",
  });

  const buildParams = (filters) => {
    const params = new URLSearchParams();
    if (filters.time_preset && filters.time_preset !== "all_time") params.set("time_preset", filters.time_preset);
    if (filters.start_datetime) params.set("start_datetime", filters.start_datetime);
    if (filters.end_datetime) params.set("end_datetime", filters.end_datetime);
    return params;
  };

  const syncUrl = (filters) => {
    const params = buildParams(filters);
    const currentTab = new URLSearchParams(window.location.search).get("tab");
    if (currentTab) params.set("tab", currentTab);
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
      const name = escapeHtml(r.name);
      const date = r.last_reported ? r.last_reported.slice(0, 10) : "—";
      return `<tr>
        <td class="rumor-lb-name" title="${name}">${name}</td>
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
      return `<tr>
        <td class="rumor-dt-desc" title="${desc}">${truncated}</td>
        <td class="rumor-dt-cat"><span class="rumor-dt-badge" style="background:${catColor}">${r.category}</span></td>
        <td class="rumor-dt-count">${r.count}</td>
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

  // ── Refresh helpers ────────────────────────────────────────────────────────
  const refreshAll = async () => {
    const filters = getFilters();
    tableState.filters = filters;
    tableState.page = 1;
    syncUrl(filters);

    const [enumeratorStats, tableData] = await Promise.all([
      fetchJSON(endpoints.enumeratorStats, filters),
      fetchJSON(endpoints.descriptionTable, filters, { page: 1 }),
    ]);

    renderEnumeratorStats(enumeratorStats);
    renderDescriptionTable(tableData);
  };

  // ── Event bindings ─────────────────────────────────────────────────────────
  const bindEvents = () => {
    if (filterElements.form) {
      filterElements.form.addEventListener("submit", (e) => { e.preventDefault(); refreshAll().catch(console.error); });
    }
    for (const el of [filterElements.preset, filterElements.start, filterElements.end]) {
      if (el) el.addEventListener("change", () => { if (!shouldApplyDateRangeChange()) return; refreshAll().catch(console.error); });
    }
    if (filterElements.reset) {
      filterElements.reset.addEventListener("click", () => {
        if (filterElements.preset) filterElements.preset.value = "all_time";
        if (filterElements.start) filterElements.start.value = "";
        if (filterElements.end) filterElements.end.value = "";
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

  // ── Init ───────────────────────────────────────────────────────────────────
  const init = async () => {
    setupDateInputs([filterElements.start, filterElements.end]);
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
