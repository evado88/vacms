/*
 * Generic AJAX list-table controller.
 *
 * Markup contract (per page):
 *   <div data-list-table>
 *     <form data-list-filter> ...filter fields, no Apply button... </form>
 *     <div data-list-slot data-endpoint="/path?component=1">
 *        ...server-rendered table + pagination fragment...
 *     </div>
 *   </div>
 *
 * Behaviour:
 *   - Text inputs auto-apply 800ms after typing stops; selects/date inputs
 *     apply immediately on change. Any filter change resets to page 1.
 *   - Sort headers and pagination links inside the slot (any <a href="?...">)
 *     are intercepted; their order_by/page is read and applied via AJAX.
 *   - Only the slot is swapped, so filter inputs never lose focus.
 */
(function () {
  "use strict";

  var DEBOUNCE_MS = 800;

  function isImmediateField(el) {
    if (el.tagName === "SELECT") return true;
    if (el.tagName === "INPUT") {
      var type = (el.getAttribute("type") || "text").toLowerCase();
      return type === "date" || type === "checkbox" || type === "radio";
    }
    return false;
  }

  function initRoot(root) {
    var form = root.querySelector("[data-list-filter]");
    var slot = root.querySelector("[data-list-slot]");
    if (!slot) return;
    var endpoint = slot.getAttribute("data-endpoint");
    if (!endpoint) return;

    var currentParams = new URLSearchParams(window.location.search);
    var state = {
      order_by: currentParams.get("order_by") || "",
      page: currentParams.get("page") || "1",
      interview_status: currentParams.get("interview_status") || "",
      death_status: currentParams.get("death_status") || "",
    };
    var timer = null;

    function collectParams() {
      var params = new URLSearchParams();
      if (form) {
        new FormData(form).forEach(function (value, key) {
          var v = (value || "").toString().trim();
          if (v) params.set(key, v);
        });
      }
      if (state.interview_status) {
        params.set("interview_status", state.interview_status);
      }
      if (state.death_status) {
        params.set("death_status", state.death_status);
      }
      if (state.order_by) params.set("order_by", state.order_by);
      if (state.page && state.page !== "1") params.set("page", state.page);
      return params;
    }

    function buildUrl() {
      var params = collectParams();
      // endpoint already carries ?component=1
      var join = endpoint.indexOf("?") === -1 ? "?" : "&";
      return endpoint + join + params.toString();
    }

    function syncDownloadLink() {
      var link = root.querySelector("[data-list-download]");
      if (!link) return;
      var params = collectParams();
      params.delete("page");
      var base = (link.getAttribute("href") || "").split("?")[0];
      var query = params.toString();
      link.setAttribute("href", query ? base + "?" + query : base);
    }

    function refresh() {
      syncDownloadLink();
      fetch(buildUrl(), {
        method: "GET",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      })
        .then(function (res) {
          if (!res.ok) throw new Error("list-table fetch failed: " + res.status);
          return res.text();
        })
        .then(function (html) {
          slot.innerHTML = html;
        })
        .catch(function () {
          /* keep last-rendered fragment on error */
        });
    }

    function onFilterChange(immediate) {
      state.page = "1";
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      if (immediate) {
        refresh();
      } else {
        timer = setTimeout(refresh, DEBOUNCE_MS);
      }
    }

    if (form) {
      form.addEventListener("input", function (event) {
        if (event.target.closest("[data-list-filter]") !== form) return;
        onFilterChange(isImmediateField(event.target));
      });
      form.addEventListener("change", function (event) {
        if (event.target.closest("[data-list-filter]") !== form) return;
        if (isImmediateField(event.target)) onFilterChange(true);
      });
      // Prevent a stray Enter keypress from doing a native full-page submit.
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        onFilterChange(true);
      });
    }

    // Sort + pagination links live inside the swapped slot.
    slot.addEventListener("click", function (event) {
      var link = event.target.closest('a[href^="?"]');
      if (!link || !slot.contains(link)) return;
      if (link.classList.contains("disabled")) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      var params = new URLSearchParams(link.getAttribute("href").slice(1));
      if (params.has("interview_status")) {
        state.interview_status = params.get("interview_status");
        state.page = "1";
      }
      if (params.has("death_status")) {
        state.death_status = params.get("death_status");
        state.page = "1";
      }
      if (params.has("order_by")) {
        state.order_by = params.get("order_by");
        state.page = "1";
      }
      if (params.has("page")) {
        state.page = params.get("page");
      }
      refresh();
    });
  }

  function init() {
    document.querySelectorAll("[data-list-table]").forEach(initRoot);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
