(function () {
  const app = document.getElementById("deathVaTimelineApp");
  if (!app) return;

  const endpoints = {
    timeline: app.dataset.timelineEndpoint,
  };

  const filterElements = {
    form: document.getElementById("vaTimelineFiltersForm"),
    preset: document.getElementById("vaTimelineFilterTimePreset"),
    start: document.getElementById("vaTimelineFilterStartDatetime"),
    end: document.getElementById("vaTimelineFilterEndDatetime"),
    sex: document.getElementById("vaTimelineFilterSex"),
    ageGroup: document.getElementById("vaTimelineFilterAgeGroup"),
    reset: document.getElementById("vaTimelineFiltersReset"),
  };

  const SERIES = {
    capture: { label: "Death → Capture (E_DEATH)", color: "#4b84ce", border: "#2f6ec2" },
    va: { label: "Death → VA Submission", color: "#d73027", border: "#b02318" },
  };

  let histogramChart = null;
  let cumulativeChart = null;
  let monthlyChart = null;

  const getFilters = () => ({
    time_preset: filterElements.preset?.value || "all_time",
    start_datetime: filterElements.start?.value || "",
    end_datetime: filterElements.end?.value || "",
    sex: filterElements.sex?.value || "",
    age_group: filterElements.ageGroup?.value || "",
  });

  const buildParams = (filters) => {
    const params = new URLSearchParams();
    if (filters.time_preset && filters.time_preset !== "all_time") params.set("time_preset", filters.time_preset);
    if (filters.start_datetime) params.set("start_datetime", filters.start_datetime);
    if (filters.end_datetime) params.set("end_datetime", filters.end_datetime);
    if (filters.sex) params.set("sex", filters.sex);
    if (filters.age_group) params.set("age_group", filters.age_group);
    params.set("tab", "va_timeline");
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
  };

  const renderCards = (payload) => {
    const cards = payload.cards || {};
    const setText = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };

    setText("vaTimelineCardTotal", cards.total_deaths ?? 0);
    const coveragePct = cards.linked_coverage_pct ?? 0;
    setText(
      "vaTimelineCardCoverage",
      `${coveragePct}% (${cards.linked_deaths ?? 0}/${cards.total_deaths ?? 0})`
    );
    setText("vaTimelineCardDeathCapture", cards.median_death_to_capture_days ?? "N/A");
    setText("vaTimelineCardDeathVa", cards.median_death_to_va_days ?? "N/A");
    setText("vaTimelineCardCaptureVa", cards.median_capture_to_va_days ?? "N/A");
  };

  const renderHistogram = (payload) => {
    const histogram = payload.delay_histogram || {};
    const canvas = document.getElementById("vaTimelineHistogramChart");
    if (!canvas || typeof Chart === "undefined") return;

    if (!histogramChart) {
      histogramChart = new Chart(canvas.getContext("2d"), {
        type: "bar",
        data: {
          labels: [],
          datasets: [
            {
              label: SERIES.capture.label,
              data: [],
              backgroundColor: SERIES.capture.color,
              borderColor: SERIES.capture.border,
              borderWidth: 1,
            },
            {
              label: SERIES.va.label,
              data: [],
              backgroundColor: SERIES.va.color,
              borderColor: SERIES.va.border,
              borderWidth: 1,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: true, position: "top" }, tooltip: { enabled: true } },
          scales: {
            x: { grid: { display: false }, title: { display: true, text: "Delay since death (days)" } },
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

    histogramChart.data.labels = histogram.labels || [];
    histogramChart.data.datasets[0].data = histogram.death_to_capture || [];
    histogramChart.data.datasets[1].data = histogram.death_to_va || [];
    histogramChart.update();

    const total = []
      .concat(histogram.death_to_capture || [], histogram.death_to_va || [])
      .reduce((acc, value) => acc + Number(value || 0), 0);
    setEmpty("vaTimelineHistogramEmpty", total === 0);
  };

  const renderCumulative = (payload) => {
    const cumulative = payload.cumulative || {};
    const canvas = document.getElementById("vaTimelineCumulativeChart");
    if (!canvas || typeof Chart === "undefined") return;

    if (!cumulativeChart) {
      cumulativeChart = new Chart(canvas.getContext("2d"), {
        type: "line",
        data: {
          labels: [],
          datasets: [
            {
              label: "% captured (E_DEATH)",
              data: [],
              borderColor: SERIES.capture.color,
              backgroundColor: "rgba(75, 132, 206, 0.15)",
              pointRadius: 3,
              tension: 0.25,
              fill: false,
            },
            {
              label: "% with VA submitted",
              data: [],
              borderColor: SERIES.va.color,
              backgroundColor: "rgba(215, 48, 39, 0.15)",
              pointRadius: 3,
              tension: 0.25,
              fill: false,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: true, position: "top" },
            tooltip: {
              enabled: true,
              callbacks: {
                label: (context) => `${context.dataset.label}: ${context.parsed.y}%`,
              },
            },
          },
          scales: {
            x: { grid: { display: false }, title: { display: true, text: "Days since death" } },
            y: {
              beginAtZero: true,
              max: 100,
              title: { display: true, text: "% of deaths" },
              grid: { display: true, color: "rgba(148,163,184,0.35)" },
            },
          },
        },
      });
    }

    cumulativeChart.data.labels = (cumulative.thresholds || []).map((t) => `≤${t}d`);
    cumulativeChart.data.datasets[0].data = cumulative.captured_pct || [];
    cumulativeChart.data.datasets[1].data = cumulative.va_submitted_pct || [];
    cumulativeChart.update();

    setEmpty("vaTimelineCumulativeEmpty", !(cumulative.denominator > 0));
  };

  const renderMonthly = (payload) => {
    const monthly = payload.monthly_medians || {};
    const canvas = document.getElementById("vaTimelineMonthlyChart");
    if (!canvas || typeof Chart === "undefined") return;

    if (!monthlyChart) {
      monthlyChart = new Chart(canvas.getContext("2d"), {
        type: "line",
        data: {
          labels: [],
          datasets: [
            {
              label: "Median death → capture (days)",
              data: [],
              borderColor: SERIES.capture.color,
              backgroundColor: "rgba(75, 132, 206, 0.15)",
              pointRadius: 3,
              tension: 0.25,
              fill: false,
              spanGaps: false,
            },
            {
              label: "Median death → VA (days)",
              data: [],
              borderColor: SERIES.va.color,
              backgroundColor: "rgba(215, 48, 39, 0.15)",
              pointRadius: 3,
              tension: 0.25,
              fill: false,
              spanGaps: false,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: true, position: "top" }, tooltip: { enabled: true } },
          scales: {
            x: { grid: { display: false }, title: { display: true, text: "Month of death" } },
            y: {
              beginAtZero: true,
              title: { display: true, text: "Median delay (days)" },
              grid: { display: true, color: "rgba(148,163,184,0.35)" },
            },
          },
        },
      });
    }

    monthlyChart.data.labels = monthly.labels || [];
    monthlyChart.data.datasets[0].data = monthly.capture_delay || [];
    monthlyChart.data.datasets[1].data = monthly.va_delay || [];
    monthlyChart.update();

    setEmpty("vaTimelineMonthlyEmpty", (monthly.labels || []).length === 0);
  };

  const renderExcludedNote = (payload) => {
    const el = document.getElementById("vaTimelineExcludedNote");
    if (!el) return;
    const excluded = payload.excluded || {};
    const unparseable = Number(excluded.unparseable_death_date || 0);
    const negative = Number(excluded.negative_delta || 0);
    const implausible = Number(excluded.implausible_delta || 0);
    const total = unparseable + negative + implausible;
    if (total === 0) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent =
      `Excluded from delay metrics: ${unparseable} record(s) without a usable date of death, ` +
      `${negative} negative interval(s), ${implausible} implausible interval(s) (> 5 years).`;
  };

  const refreshAll = async () => {
    const filters = getFilters();
    const payload = await fetchJSON(endpoints.timeline, filters);
    renderCards(payload);
    renderHistogram(payload);
    renderCumulative(payload);
    renderMonthly(payload);
    renderExcludedNote(payload);
  };

  const bindEvents = () => {
    if (filterElements.form) {
      filterElements.form.addEventListener("submit", (event) => {
        event.preventDefault();
        refreshAll().catch((err) => console.error(err));
      });
    }

    [filterElements.preset, filterElements.start, filterElements.end].forEach((el) => {
      if (!el) return;
      el.addEventListener("change", () => {
        if (!shouldApplyDateRangeChange()) return;
        refreshAll().catch((err) => console.error(err));
      });
    });

    [filterElements.sex, filterElements.ageGroup].forEach((el) => {
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
        refreshAll().catch((err) => console.error(err));
      });
    }
  };

  const init = async () => {
    setupDateInputs([filterElements.start, filterElements.end]);
    bindEvents();
    await refreshAll();
  };

  const resizeVisuals = () => {
    if (histogramChart) histogramChart.resize();
    if (cumulativeChart) cumulativeChart.resize();
    if (monthlyChart) monthlyChart.resize();
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
