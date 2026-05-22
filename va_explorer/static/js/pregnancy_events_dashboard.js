(function () {
  const app = document.getElementById("pregnancyEventsDashboardApp");
  if (!app) return;

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
  let mapController = null;

  const mapState = {
    map: null,
    layer: null,
    geojson: null,
    colors: ["#4575b4", "#74add1", "#abd9e9", "#e0f3f8", "#fee090", "#fdae61", "#f46d43", "#d73027"],
  };

  const getEl = (id) => document.getElementById(id);

  const filterElements = {
    form: getEl("peFiltersForm"),
    outcome: getEl("peFilterPregnancyOutcome"),
    preset: getEl("peFilterTimePreset"),
    start: getEl("peFilterStartDatetime"),
    end: getEl("peFilterEndDatetime"),
    mapViewSelect: getEl("peMapViewSelect"),
    mapViewHidden: getEl("peFilterMapView"),
    reset: getEl("peFiltersReset"),
    birthCount: getEl("peBirthModeCount"),
    birthPct: getEl("peBirthModePercentage"),
    placeCount: getEl("pePlaceBirthModeCount"),
    placePct: getEl("pePlaceBirthModePercentage"),
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
  mapController =
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
          },
          includeSelectionInRequest: false,
          styleVariant: "va",
          noDataMessage: "No mapped pregnancy events in current filter range.",
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

  const renderSummary = (data) => {
    setText("peCardLastDataUpdate", data.card_last_data_update || "N/A");
    setText("peCardLastEventDate", data.card_last_event_date || "N/A");
    setText("peCardNumberOfEvents", data.card_number_of_events ?? 0);
    setText("peCardMultipleBirthPct", `${data.card_multiple_birth_pct ?? 0}%`);
    setEmptyState("peSummaryEmpty", (data.card_number_of_events ?? 0) === 0);
  };

  const renderKpis = (data) => {
    setText("peKpiMeanAge", data.mean_age ?? 0);
    setText("peKpiHivPct", `${data.hiv_positive_pct ?? 0}%`);
    setEmptyState("peKpisEmpty", (data.mean_age ?? 0) === 0 && (data.hiv_positive_pct ?? 0) === 0);
  };

  const ensureLineChart = () => {
    if (chartState.trend) return chartState.trend;
    const canvas = getEl("peTrendChart");
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
    const chart = ensureLineChart();
    if (!chart) return;
    chart.data.labels = data.labels || [];
    chart.data.datasets[0].data = data.data || [];
    chart.update();
    const sum = (data.data || []).reduce((acc, value) => acc + Number(value || 0), 0);
    setEmptyState("peTrendEmpty", sum === 0);
  };

  const currentBirthMode = () => (filterElements.birthPct?.checked ? "percentage" : "count");
  const currentPlaceMode = () => (filterElements.placePct?.checked ? "percentage" : "count");

  const renderBirthOutcomes = (payload) => {
    chartState.birthOutcomesPayload = payload;
    const chart = ensureBarChart("birthOutcomes", "peBirthOutcomesChart", {
      type: "bar",
      data: { labels: [], datasets: [{ label: "Birth Outcomes", data: [], backgroundColor: ["#2d6cdf", "#f46d43"], borderColor: ["#2d6cdf", "#f46d43"], borderWidth: 1 }] },
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
    const mode = currentBirthMode();
    chart.data.labels = payload.labels || [];
    chart.data.datasets[0].data = mode === "percentage" ? (payload.percentage_data || []) : (payload.count_data || []);
    chart.options.scales.y.title.text = mode === "percentage" ? "Percentage (%)" : "Count";
    chart.update();
    const sum = (payload.count_data || []).reduce((acc, value) => acc + Number(value || 0), 0);
    setEmptyState("peBirthOutcomesEmpty", sum === 0);
  };

  const renderGestationalAge = (payload) => {
    const chart = ensureBarChart("gestationalAge", "peGestationalAgeChart", {
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
    setEmptyState("peGestationalAgeEmpty", sum === 0);
  };

  const renderAncVisits = (payload) => {
    const chart = ensureBarChart("ancVisits", "peAncVisitsChart", {
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
    setEmptyState("peAncVisitsEmpty", sum === 0);
  };

  const renderPlaceOfBirth = (payload) => {
    chartState.placeOfBirthPayload = payload;
    const chart = ensureBarChart("placeOfBirth", "pePlaceOfBirthChart", {
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
    setEmptyState("pePlaceOfBirthEmpty", sum === 0);
  };

  const normalizeGeoName = (value) => (value || "").toString().toLowerCase().replace(/province|district/g, "").replace(/\s+/g, " ").trim();
  const buildLookup = (rows) => {
    const map = new Map();
    (rows || []).forEach((row) => {
      const name = normalizeGeoName(row.name);
      if (name) map.set(name, Number(row.count || 0));
    });
    return map;
  };
  const computeBins = (values) => {
    const nonZero = values.filter((v) => v > 0);
    if (!nonZero.length) return [0];
    const max = Math.max(...nonZero);
    const steps = Math.min(mapState.colors.length, 6);
    const bins = [1];
    const width = Math.max(1, Math.ceil(max / steps));
    for (let i = 1; i <= steps; i += 1) bins.push(i * width);
    return bins;
  };
  const getColorForCount = (count, bins) => {
    if (!count || count <= 0) return "#c0c0c0";
    for (let i = 0; i < bins.length - 1; i += 1) {
      if (count >= bins[i] && count <= bins[i + 1]) return mapState.colors[Math.min(i, mapState.colors.length - 1)];
    }
    return mapState.colors[Math.min(bins.length - 2, mapState.colors.length - 1)];
  };
  const renderMapLegend = (bins) => {
    const legend = getEl("peMapLegend");
    if (!legend) return;
    if (!bins || bins.length <= 1) {
      legend.innerHTML = "<div>No mapped outcomes in current filter range.</div>";
      setEmptyState("peMapEmpty", true);
      return;
    }
    let html = "<div><svg width='18' height='14'><rect fill='#c0c0c0' width='14' height='14'></rect></svg>0</div>";
    for (let i = 0; i < bins.length - 1; i += 1) {
      const label = i === bins.length - 2 ? `${bins[i]}+` : `${bins[i]} - ${bins[i + 1]}`;
      html += `<div><svg width='18' height='14'><rect fill='${mapState.colors[i]}' width='14' height='14'></rect></svg>${label}</div>`;
    }
    legend.innerHTML = html;
    setEmptyState("peMapEmpty", false);
  };

  const ensureMap = async () => {
    if (mapState.map || typeof L === "undefined") return;
    mapState.map = L.map("peMapContainer", { maxBounds: [[-6, 20], [-20, 34]] }).setView([-13, 27], 6);
    mapState.map.attributionControl.setPrefix("");
    mapState.map.keyboard.disable();
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 10,
      // OSM public tile servers block requests when the browser omits Referer.
      referrerPolicy: "strict-origin-when-cross-origin",
      attribution: '&copy; OpenStreetMap contributors',
    }).addTo(mapState.map);

    const res = await fetch(`${window.location.origin}/static/data/zambia_geojson.json`);
    mapState.geojson = await res.json();
  };

  const renderMap = async (payload) => {
    await ensureMap();
    if (!mapState.map || !mapState.geojson) return;

    const mapView = payload.map_view === "District" ? "District" : "Province";
    const lookup = buildLookup(payload.counts || []);
    const bins = computeBins(Array.from(lookup.values()));
    const geojson = JSON.parse(JSON.stringify(mapState.geojson));
    geojson.features = geojson.features.filter((f) => {
      const level = f?.properties?.area_level_label;
      return level === "Country" || level === mapView;
    });

    if (mapState.layer) mapState.map.removeLayer(mapState.layer);

    mapState.layer = L.geoJson(geojson, {
      style: (feature) => {
        const level = feature?.properties?.area_level_label;
        if (level === "Country") {
          return { weight: 2.5, opacity: 1, color: "grey", stroke: true, fillOpacity: 0 };
        }
        const areaName = normalizeGeoName(feature?.properties?.area_name);
        const count = lookup.get(areaName) || 0;
        const color = getColorForCount(count, bins);
        return { stroke: true, weight: 2, color, opacity: 1, fillColor: color, fillOpacity: 0.7 };
      },
      onEachFeature: (feature, layer) => {
        const level = feature?.properties?.area_level_label;
        if (level === "Country") return;
        const areaNameRaw = feature?.properties?.area_name || "";
        const areaName = normalizeGeoName(areaNameRaw);
        const count = lookup.get(areaName) || 0;
        layer.bindTooltip(`<div class="mapTooltip"><h4>${areaNameRaw} ${level}</h4><p>${count}</p></div>`);
      },
    }).addTo(mapState.map);

    renderMapLegend(bins);
  };

  const refreshMapOnly = async () => {
    const filters = getFilters();
    syncFilterHiddenFields();
    syncUrl(filters);
    if (mapController) {
      await mapController.refresh(filters);
      return;
    }
    const mapData = await fetchJSON(endpoints.map, filters);
    await renderMap(mapData);
  };

  const refreshAll = async () => {
    const filters = getFilters();
    syncFilterHiddenFields();
    syncUrl(filters);

    const requests = [
      fetchJSON(endpoints.summary, filters),
      fetchJSON(endpoints.trend, filters),
      fetchJSON(endpoints.birthOutcomes, filters),
      fetchJSON(endpoints.gestationalAge, filters),
      fetchJSON(endpoints.ancVisits, filters),
      fetchJSON(endpoints.kpis, filters),
      fetchJSON(endpoints.placeOfBirth, filters),
    ];
    if (!mapController) requests.push(fetchJSON(endpoints.map, filters));

    const [summary, trend, birthOutcomes, gestAge, anc, kpis, place, map] = await Promise.all(requests);

    renderSummary(summary);
    renderTrend(trend);
    renderBirthOutcomes(birthOutcomes);
    renderGestationalAge(gestAge);
    renderAncVisits(anc);
    renderKpis(kpis);
    renderPlaceOfBirth(place);
    if (mapController) {
      await mapController.refresh(filters);
    } else {
      await renderMap(map);
    }
  };

  const bindEvents = () => {
    if (filterElements.form) {
      filterElements.form.addEventListener("submit", (event) => {
        event.preventDefault();
        refreshAll().catch((err) => console.error(err));
      });
    }

    [filterElements.outcome, filterElements.preset, filterElements.start, filterElements.end].forEach((el) => {
      if (!el) return;
      el.addEventListener("change", () => refreshAll().catch((err) => console.error(err)));
    });

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
    bindEvents();
    await refreshAll();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => init().catch((err) => console.error(err)));
  } else {
    init().catch((err) => console.error(err));
  }
})();
