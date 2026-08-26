(function () {
  const app = document.getElementById("deathsDashboardApp");
  if (!app) return;

  const endpoints = {
    summary: app.dataset.summaryEndpoint,
    trend: app.dataset.trendEndpoint,
    map: app.dataset.mapEndpoint,
    ageSex: app.dataset.ageSexEndpoint,
    place: app.dataset.placeEndpoint,
    signals: app.dataset.signalsEndpoint,
    timeliness: app.dataset.timelinessEndpoint,
  };

  const filterElements = {
    form: document.getElementById("deathsFiltersForm"),
    preset: document.getElementById("deathsFilterTimePreset"),
    start: document.getElementById("deathsFilterStartDatetime"),
    end: document.getElementById("deathsFilterEndDatetime"),
    sex: document.getElementById("deathsFilterSex"),
    ageGroup: document.getElementById("deathsFilterAgeGroup"),
    codedOnly: document.getElementById("deathsFilterCodedOnly"),
    mapView: document.getElementById("deathsMapViewSelect"),
    reset: document.getElementById("deathsFiltersReset"),
    ageSexCount: document.getElementById("deathsAgeSexModeCount"),
    ageSexPercentage: document.getElementById("deathsAgeSexModePercentage"),
    placeCount: document.getElementById("deathsPlaceModeCount"),
    placePercentage: document.getElementById("deathsPlaceModePercentage"),
  };

  let trendChart = null;
  let ageSexChart = null;
  let placeChart = null;
  let timelinessChart = null;
  let ageSexPayload = null;
  let placePayload = null;
  let mapSelection = { geography_level: "", geography_value: "" };

  const currentAgeSexMode = () =>
    filterElements.ageSexPercentage?.checked ? "percentage" : "count";
  const currentPlaceMode = () =>
    filterElements.placePercentage?.checked ? "percentage" : "count";

  const getFilters = () => ({
    time_preset: filterElements.preset?.value || "all_time",
    start_datetime: filterElements.start?.value || "",
    end_datetime: filterElements.end?.value || "",
    sex: filterElements.sex?.value || "",
    age_group: filterElements.ageGroup?.value || "",
    coded_only: filterElements.codedOnly?.checked ? "1" : "",
    map_view: filterElements.mapView?.value || "Province",
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
    if (filters.sex) params.set("sex", filters.sex);
    if (filters.age_group) params.set("age_group", filters.age_group);
    if (filters.coded_only) params.set("coded_only", filters.coded_only);
    if (filters.map_view) params.set("map_view", filters.map_view);
    const selection = getEffectiveSelection();
    if (selection.geography_level && selection.geography_value) {
      params.set("geography_level", selection.geography_level);
      params.set("geography_value", selection.geography_value);
    }
    return params;
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
          containerId: "deathsMapContainer",
          legendId: "deathsMapLegend",
          breadcrumbId: "deathsMapBreadcrumb",
          emptyStateId: "deathsMapEmpty",
          endpoint: endpoints.map,
          buildParams,
          onSelectionChange: (selection) => {
            mapSelection = {
              geography_level: selection?.geography_level || "",
              geography_value: selection?.geography_value || "",
            };
            refreshDataOnly().catch((err) => console.error(err));
          },
          includeSelectionInRequest: true,
          styleVariant: "va",
          fitToDataBounds: false,
          noDataMessage: "No mapped deaths in current filter range.",
          userCanViewPII,
          gpsFollowsDrilldown: true,
        })
      : null;

  const setEmpty = (id, isEmpty) => {
    const el = document.getElementById(id);
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

  const renderSummary = (payload) => {
    const setText = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };

    setText("deathsCardLastDataUpdate", payload.death_card_last_data_update || "N/A");
    setText("deathsCardTotalEvents", payload.death_card_total_events ?? 0);
    setText("deathsCardMeanAge", payload.death_card_mean_age ?? "N/A");
    setText("deathsCardUnder5Pct", `${payload.death_card_under_5_pct ?? 0}%`);
    setText("deathsCardMedianDelayDays", payload.death_card_median_delay_days ?? "N/A");
  };

  const renderSignals = (payload) => {
    const formatDiff = (value) => {
      const num = Number(value || 0);
      return `${num >= 0 ? "+" : ""}${num}`;
    };

    const formatPct = (value) => {
      if (value == null) return "N/A";
      const num = Number(value);
      return `${num >= 0 ? "+" : ""}${num.toFixed(1)}%`;
    };

    const setSignalText = (id, metric) => {
      const el = document.getElementById(id);
      if (!el) return;
      const status = metric?.flag ? "Alert" : "Normal";
      el.textContent = `Diff: ${formatDiff(metric?.difference)} | Change: ${formatPct(metric?.percent_change)} | Status: ${status}`;
    };

    setSignalText("signalAll7d", payload.all_deaths_7d);
    setSignalText("signalAll30d", payload.all_deaths_30d);
    setSignalText("signalU57d", payload.under5_deaths_7d);
    setSignalText("signalU530d", payload.under5_deaths_30d);
  };

  const renderTrend = (payload) => {
    const canvas = document.getElementById("deathsTrendChart");
    if (!canvas || typeof Chart === "undefined") return;

    if (!trendChart) {
      trendChart = new Chart(canvas.getContext("2d"), {
        type: "line",
        data: {
          labels: [],
          datasets: [{
            label: "Deaths",
            data: [],
            borderColor: "#d73027",
            backgroundColor: "rgba(215, 48, 39, 0.2)",
            pointRadius: 2,
            tension: 0.25,
            fill: false,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: true, position: "top" }, tooltip: { enabled: true } },
          scales: {
            x: { title: { display: true, text: "Month" }, grid: { display: true, color: "rgba(148, 163, 184, 0.35)" } },
            y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Count of deaths" }, grid: { display: true, color: "rgba(148, 163, 184, 0.35)" } },
          },
        },
      });
    }

    trendChart.data.labels = payload.labels || [];
    trendChart.data.datasets[0].data = payload.data || [];
    trendChart.update();

    const total = (payload.data || []).reduce((acc, value) => acc + Number(value || 0), 0);
    setEmpty("deathsTrendEmpty", total === 0);
  };

  const refreshMapOnly = async (filters) => {
    if (mapController) {
      await mapController.refresh(filters);
      return;
    }
    const payload = await fetchJSON(endpoints.map, filters);
    setEmpty("deathsMapEmpty", (payload.counts || []).length === 0);
  };

  const renderAgeSex = (payload) => {
    ageSexPayload = payload;
    const canvas = document.getElementById("deathsAgeSexChart");
    if (!canvas || typeof Chart === "undefined") return;

    if (!ageSexChart) {
      ageSexChart = new Chart(canvas.getContext("2d"), {
        type: "bar",
        data: {
          labels: [],
          datasets: [
            { label: "Female", data: [], backgroundColor: "#f46d43", borderColor: "#d73027", borderWidth: 1 },
            { label: "Male", data: [], backgroundColor: "#4b84ce", borderColor: "#2f6ec2", borderWidth: 1 },
          ],
        },
        options: {
          indexAxis: "y",
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: true, position: "top" }, tooltip: { enabled: true } },
          scales: {
            x: {
              stacked: true,
              beginAtZero: true,
              ticks: { precision: 0 },
              title: { display: true, text: "Count of deaths" },
              grid: { display: true, color: "rgba(148, 163, 184, 0.35)" },
            },
            y: { stacked: true, grid: { display: false } },
          },
        },
      });
    }

    const labels = payload.labels || [];
    const female = payload.female || [];
    const male = payload.male || [];
    const mode = currentAgeSexMode();

    let femaleData = female;
    let maleData = male;

    if (mode === "percentage") {
      femaleData = [];
      maleData = [];
      for (let i = 0; i < labels.length; i += 1) {
        const f = Number(female[i] || 0);
        const m = Number(male[i] || 0);
        const total = f + m;
        femaleData.push(total ? Number(((f / total) * 100).toFixed(1)) : 0);
        maleData.push(total ? Number(((m / total) * 100).toFixed(1)) : 0);
      }
      ageSexChart.options.scales.x.max = 100;
      ageSexChart.options.scales.x.title.text = "Percentage (%)";
    } else {
      ageSexChart.options.scales.x.max = undefined;
      ageSexChart.options.scales.x.title.text = "Count of deaths";
    }

    ageSexChart.data.labels = labels;
    ageSexChart.data.datasets[0].data = femaleData;
    ageSexChart.data.datasets[1].data = maleData;
    ageSexChart.update();

    const total = []
      .concat(payload.male || [], payload.female || [])
      .reduce((acc, value) => acc + Number(value || 0), 0);
    setEmpty("deathsAgeSexEmpty", total === 0);
  };

  const renderPlace = (payload) => {
    placePayload = payload;
    const canvas = document.getElementById("deathsPlaceChart");
    if (!canvas || typeof Chart === "undefined") return;

    if (!placeChart) {
      placeChart = new Chart(canvas.getContext("2d"), {
        type: "bar",
        data: {
          labels: [],
          datasets: [{
            label: "Place of Death",
            data: [],
            backgroundColor: "#8e44ad",
            borderColor: "#6c3483",
            borderWidth: 1,
          }],
        },
        options: {
          indexAxis: "y",
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false }, tooltip: { enabled: true } },
          scales: {
            x: {
              beginAtZero: true,
              ticks: { precision: 0 },
              title: { display: true, text: "Count" },
              grid: { display: true, color: "rgba(148,163,184,0.35)" },
            },
            y: { grid: { display: false } },
          },
        },
      });
    }

    const mode = currentPlaceMode();
    placeChart.data.labels = payload.labels || [];
    placeChart.data.datasets[0].data =
      mode === "percentage" ? (payload.percentage_data || []) : (payload.count_data || []);
    placeChart.options.scales.x.title.text = mode === "percentage" ? "Percentage (%)" : "Count";
    if (mode === "percentage") {
      placeChart.options.scales.x.max = 100;
    } else {
      placeChart.options.scales.x.max = undefined;
    }
    placeChart.update();

    const total = (payload.count_data || []).reduce((acc, value) => acc + Number(value || 0), 0);
    setEmpty("deathsPlaceEmpty", total === 0);
  };

  const renderTimeliness = (payload) => {
    const canvas = document.getElementById("deathsTimelinessChart");
    if (!canvas || typeof Chart === "undefined") return;

    if (!timelinessChart) {
      timelinessChart = new Chart(canvas.getContext("2d"), {
        type: "bar",
        data: {
          labels: [],
          datasets: [{
            label: "Deaths",
            data: [],
            backgroundColor: "#4b84ce",
            borderColor: "#2f6ec2",
            borderWidth: 1,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false }, tooltip: { enabled: true } },
          scales: {
            x: { grid: { display: false }, title: { display: true, text: "Delay (days)" } },
            y: {
              beginAtZero: true,
              ticks: { precision: 0 },
              title: { display: true, text: "Count of deaths" },
              grid: { display: true, color: "rgba(148,163,184,0.35)" },
            },
          },
        },
      });
    }

    timelinessChart.data.labels = payload.labels || [];
    timelinessChart.data.datasets[0].data = payload.data || [];
    timelinessChart.update();

    const total = (payload.data || []).reduce((acc, value) => acc + Number(value || 0), 0);
    setEmpty("deathsTimelinessEmpty", total === 0);
  };

  const refreshDataOnly = async () => {
    const filters = getFilters();
    const [
      summaryPayload,
      trendPayload,
      ageSexPayloadResp,
      placePayloadResp,
      signalsPayload,
      timelinessPayloadResp,
    ] = await Promise.all([
      fetchJSON(endpoints.summary, filters),
      fetchJSON(endpoints.trend, filters),
      fetchJSON(endpoints.ageSex, filters),
      fetchJSON(endpoints.place, filters),
      fetchJSON(endpoints.signals, filters),
      fetchJSON(endpoints.timeliness, filters),
    ]);

    renderSummary(summaryPayload);
    renderSignals(signalsPayload);
    renderTrend(trendPayload);
    renderAgeSex(ageSexPayloadResp);
    renderPlace(placePayloadResp);
    renderTimeliness(timelinessPayloadResp);
  };

  const refreshAll = async () => {
    const filters = getFilters();
    await refreshDataOnly();
    await refreshMapOnly(filters);
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
    [
      filterElements.sex,
      filterElements.ageGroup,
      filterElements.codedOnly,
      filterElements.mapView,
    ].forEach((el) => {
      if (!el) return;
      el.addEventListener("change", () => refreshAll().catch((err) => console.error(err)));
    });

    if (filterElements.reset) {
      filterElements.reset.addEventListener("click", () => {
        if (filterElements.preset) filterElements.preset.value = "all_time";
        if (filterElements.start) filterElements.start.value = "";
        if (filterElements.end) filterElements.end.value = "";
        if (filterElements.sex) filterElements.sex.value = "";
        if (filterElements.ageGroup) filterElements.ageGroup.value = "";
        if (filterElements.codedOnly) filterElements.codedOnly.checked = false;
        if (filterElements.mapView) filterElements.mapView.value = "Province";
        if (filterElements.ageSexCount) filterElements.ageSexCount.checked = true;
        if (filterElements.placeCount) filterElements.placeCount.checked = true;
        refreshAll().catch((err) => console.error(err));
      });
    }

    const rerenderAgeSex = () => {
      if (ageSexPayload) renderAgeSex(ageSexPayload);
    };
    if (filterElements.ageSexCount) {
      filterElements.ageSexCount.addEventListener("change", rerenderAgeSex);
    }
    if (filterElements.ageSexPercentage) {
      filterElements.ageSexPercentage.addEventListener("change", rerenderAgeSex);
    }

    const rerenderPlace = () => {
      if (placePayload) renderPlace(placePayload);
    };
    if (filterElements.placeCount) {
      filterElements.placeCount.addEventListener("change", rerenderPlace);
    }
    if (filterElements.placePercentage) {
      filterElements.placePercentage.addEventListener("change", rerenderPlace);
    }

  };

  const init = async () => {
    setupDateInputs([filterElements.start, filterElements.end]);
    const today = new Date().toISOString().slice(0, 10);
    if (filterElements.start && !filterElements.start.value) filterElements.start.value = today;
    if (filterElements.end && !filterElements.end.value) filterElements.end.value = today;
    bindEvents();
    await refreshAll();
  };

  const resizeVisuals = () => {
    if (trendChart) trendChart.resize();
    if (ageSexChart) ageSexChart.resize();
    if (placeChart) placeChart.resize();
    if (timelinessChart) timelinessChart.resize();
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
