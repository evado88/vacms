(function() {
  var mapEl = document.getElementById('regionalMap');
  var hasMap = Boolean(mapEl && window.L);

  var componentsEl = document.getElementById('regionalOperationsComponents');
  var componentUrls = {
    filters: componentsEl ? componentsEl.getAttribute('data-filters-url') : '',
    csa: componentsEl ? componentsEl.getAttribute('data-csa-url') : '',
    mso: componentsEl ? componentsEl.getAttribute('data-mso-url') : ''
  };
  var csaComponentEl = document.getElementById('regionalCsaComponent');
  var msoComponentEl = document.getElementById('regionalMsoComponent');
  var csaSearchInputEl = document.getElementById('csaNameSearch');
  var csaSupervisorSearchInputEl = document.getElementById('csaSupervisorSearch');
  var csaDistrictSearchInputEl = document.getElementById('csaDistrictSearch');
  var csaWardSearchInputEl = document.getElementById('csaWardSearch');
  var msoSearchInputEl = document.getElementById('msoNameSearch');
  var msoProvinceSearchInputEl = document.getElementById('msoProvinceSearch');
  var focusState = {
    activeCsaSearchId: null,
    csaCaretPos: null,
    activeMsoSearchId: null,
    msoCaretPos: null
  };
  var tableSortDefaults = {
    csa_sort: 'visits',
    csa_dir: 'desc',
    csa_search: '',
    csa_supervisor_search: '',
    csa_district_search: '',
    csa_ward_search: '',
    csa_page: '1',
    mso_sort: 'death_events',
    mso_dir: 'desc',
    mso_search: '',
    mso_province_search: '',
    mso_page: '1'
  };
  var tableSortState = {
    csa_sort: csaComponentEl ? (csaComponentEl.getAttribute('data-csa-sort') || tableSortDefaults.csa_sort) : tableSortDefaults.csa_sort,
    csa_dir: csaComponentEl ? (csaComponentEl.getAttribute('data-csa-dir') || tableSortDefaults.csa_dir) : tableSortDefaults.csa_dir,
    csa_search: csaSearchInputEl ? (csaSearchInputEl.value || '') : '',
    csa_supervisor_search: csaSupervisorSearchInputEl ? (csaSupervisorSearchInputEl.value || '') : '',
    csa_district_search: csaDistrictSearchInputEl ? (csaDistrictSearchInputEl.value || '') : '',
    csa_ward_search: csaWardSearchInputEl ? (csaWardSearchInputEl.value || '') : '',
    csa_page: csaComponentEl ? (csaComponentEl.getAttribute('data-csa-page') || tableSortDefaults.csa_page) : tableSortDefaults.csa_page,
    mso_sort: msoComponentEl ? (msoComponentEl.getAttribute('data-mso-sort') || tableSortDefaults.mso_sort) : tableSortDefaults.mso_sort,
    mso_dir: msoComponentEl ? (msoComponentEl.getAttribute('data-mso-dir') || tableSortDefaults.mso_dir) : tableSortDefaults.mso_dir,
    mso_search: msoSearchInputEl ? (msoSearchInputEl.value || '') : '',
    mso_province_search: msoProvinceSearchInputEl ? (msoProvinceSearchInputEl.value || '') : '',
    mso_page: msoComponentEl ? (msoComponentEl.getAttribute('data-mso-page') || tableSortDefaults.mso_page) : tableSortDefaults.mso_page
  };

  var label = document.getElementById('geographyTimeLabel');
  var mapUrl = mapEl ? mapEl.getAttribute('data-map-url') : '';
  var geojsonUrl = window.location.origin + '/static/data/zambia_geojson.json';

  var colorScale = [
    '#4575b4', '#74add1', '#abd9e9', '#e0f3f8', '#ffffbf',
    '#fee090', '#fdae61', '#f46d43', '#d73027'
  ];

  var state = {
    map: null,
    layer: null,
    geojson: null,
    geographic_province_sums: null,
    geographic_district_sums: null,
    geoScale: null,
    hasFit: false
  };

  var mapping = {
    timeAll: 'All Time',
    time30: 'Last 30 days',
    time7: 'Last 7 days',
    time24: 'Last 24 hours'
  };

  function normalizePresetId(presetId) {
    return String(presetId || '').replace(/_mso$/, '');
  }

  function getEl(id) {
    return document.getElementById(id);
  }

  function getActiveFilterSuffix() {
    var activePanel = document.querySelector(".dashboard-shell[data-shell='home'] .dashboard-tab-panel.show.active");
    var tabKey = activePanel && activePanel.dataset ? activePanel.dataset.tabPanel : "";
    if (tabKey === "mso_supervision" && getEl('geographyFilterSelect_mso')) {
      return "_mso";
    }
    return "";
  }

  function getFilterEl(baseId) {
    var suffix = getActiveFilterSuffix();
    return getEl(baseId + suffix) || getEl(baseId);
  }

  function isCsaSearchInputId(id) {
    return ['csaNameSearch', 'csaSupervisorSearch', 'csaDistrictSearch', 'csaWardSearch'].indexOf(id) !== -1;
  }

  function getCsaSearchStateKey(id) {
    return {
      csaNameSearch: 'csa_search',
      csaSupervisorSearch: 'csa_supervisor_search',
      csaDistrictSearch: 'csa_district_search',
      csaWardSearch: 'csa_ward_search'
    }[id];
  }

  function isMsoSearchInputId(id) {
    return ['msoNameSearch', 'msoProvinceSearch'].indexOf(id) !== -1;
  }

  function getMsoSearchStateKey(id) {
    return {
      msoNameSearch: 'mso_search',
      msoProvinceSearch: 'mso_province_search'
    }[id];
  }

  function getSelectedPreset() {
    var suffix = getActiveFilterSuffix();
    return (
      document.querySelector('input[name="timePreset' + suffix + '"]:checked')
      || document.querySelector('input[name="timePreset"]:checked')
    );
  }

  function getDateRange() {
    var startInput = getFilterEl('timeStartDate');
    var endInput = getFilterEl('timeEndDate');
    var startVal = startInput ? startInput.value : '';
    var endVal = endInput ? endInput.value : '';
    return {start: startVal, end: endVal};
  }

  function getFilterParams() {
    var params = new URLSearchParams();
    var geoSelect = getFilterEl('geographyFilterSelect');
    var sourceSelect = getEl('msoSourceSelect');
    var csaSearchInput = getEl('csaNameSearch');
    var csaSupervisorSearchInput = getEl('csaSupervisorSearch');
    var csaDistrictSearchInput = getEl('csaDistrictSearch');
    var csaWardSearchInput = getEl('csaWardSearch');
    var msoSearchInput = getEl('msoNameSearch');
    var msoProvinceSearchInput = getEl('msoProvinceSearch');
    var selectedPreset = getSelectedPreset();
    var range = getDateRange();

    if (geoSelect && geoSelect.value) params.set('geo', geoSelect.value);
    if (sourceSelect && sourceSelect.value) params.set('source', sourceSelect.value);
    if (selectedPreset && selectedPreset.id) params.set('time_preset', normalizePresetId(selectedPreset.id));
    if (range.start) params.set('start_date', range.start);
    if (range.end) params.set('end_date', range.end);
    if (csaSearchInput) tableSortState.csa_search = csaSearchInput.value.trim();
    if (csaSupervisorSearchInput) tableSortState.csa_supervisor_search = csaSupervisorSearchInput.value.trim();
    if (csaDistrictSearchInput) tableSortState.csa_district_search = csaDistrictSearchInput.value.trim();
    if (csaWardSearchInput) tableSortState.csa_ward_search = csaWardSearchInput.value.trim();
    if (msoSearchInput) tableSortState.mso_search = msoSearchInput.value.trim();
    if (msoProvinceSearchInput) tableSortState.mso_province_search = msoProvinceSearchInput.value.trim();
    if (tableSortState.csa_sort) params.set('csa_sort', tableSortState.csa_sort);
    if (tableSortState.csa_dir) params.set('csa_dir', tableSortState.csa_dir);
    if (tableSortState.csa_search) params.set('csa_search', tableSortState.csa_search);
    if (tableSortState.csa_supervisor_search) params.set('csa_supervisor_search', tableSortState.csa_supervisor_search);
    if (tableSortState.csa_district_search) params.set('csa_district_search', tableSortState.csa_district_search);
    if (tableSortState.csa_ward_search) params.set('csa_ward_search', tableSortState.csa_ward_search);
    if (tableSortState.csa_page) params.set('csa_page', tableSortState.csa_page);
    if (tableSortState.mso_sort) params.set('mso_sort', tableSortState.mso_sort);
    if (tableSortState.mso_dir) params.set('mso_dir', tableSortState.mso_dir);
    if (tableSortState.mso_search) params.set('mso_search', tableSortState.mso_search);
    if (tableSortState.mso_province_search) params.set('mso_province_search', tableSortState.mso_province_search);
    if (tableSortState.mso_page) params.set('mso_page', tableSortState.mso_page);

    return params;
  }

  function refreshComponent(containerId, url) {
    var container = getEl(containerId);
    if (!container || !url) return Promise.resolve();

    var params = getFilterParams();
    var requestUrl = url + (params.toString() ? '?' + params.toString() : '');

    return fetch(requestUrl, {method: 'GET'})
      .then(function(res) {
        if (!res.ok) throw new Error('Failed to load component');
        return res.text();
      })
      .then(function(html) {
        container.innerHTML = html;
        // Keep data-loaded="1" in sync so DashboardLoader.loadComponentOnce
        // knows this slot is populated and skips re-fetching on tab switch.
        container.dataset.loaded = '1';
        if (containerId === 'regionalCsaComponent' && focusState.activeCsaSearchId) {
          var refreshedSearch = getEl(focusState.activeCsaSearchId);
          if (refreshedSearch) {
            refreshedSearch.focus();
            var pos = typeof focusState.csaCaretPos === 'number'
              ? Math.min(focusState.csaCaretPos, refreshedSearch.value.length)
              : refreshedSearch.value.length;
            refreshedSearch.setSelectionRange(pos, pos);
          }
        }
        if (containerId === 'regionalMsoComponent' && focusState.activeMsoSearchId) {
          var refreshedMsoSearch = getEl(focusState.activeMsoSearchId);
          if (refreshedMsoSearch) {
            refreshedMsoSearch.focus();
            var msoPos = typeof focusState.msoCaretPos === 'number'
              ? Math.min(focusState.msoCaretPos, refreshedMsoSearch.value.length)
              : refreshedMsoSearch.value.length;
            refreshedMsoSearch.setSelectionRange(msoPos, msoPos);
          }
        }
      })
      .catch(function() {
        // Keep last-rendered component if refresh fails.
      });
  }

  function refreshDataComponents(options) {
    var opts = options || {csa: true, mso: true};
    var requests = [];

    if (opts.filters) {
      requests.push(refreshComponent('regionalFiltersComponent', componentUrls.filters));
    }
    if (opts.csa) {
      requests.push(refreshComponent('regionalCsaComponent', componentUrls.csa));
    }
    if (opts.mso) {
      requests.push(refreshComponent('regionalMsoComponent', componentUrls.mso));
    }

    return Promise.all(requests);
  }

  function updateLabel() {
    if (!label) return;

    var range = getDateRange();
    if (range.start && range.end) {
      label.textContent = 'Geography: ' + range.start + ' - ' + range.end;
      return;
    }

    var selected = getSelectedPreset();
    var presetId = normalizePresetId(selected && selected.id);
    if (!presetId || !mapping[presetId]) return;
    label.textContent = 'Geography: ' + mapping[presetId];
  }

  function getBorderType() {
    var geoSelect = getFilterEl('geographyFilterSelect');
    if (!geoSelect || !geoSelect.value || geoSelect.value === 'national') {
      return 'Province';
    }
    return 'District';
  }

  function computeGeoScale(geoSums) {
    if (!geoSums || !geoSums.length) return null;
    var geoMax = Math.max.apply(null, geoSums.map(function(item) { return +item.count; })) + 100;
    var geoMin = 1;
    var n = 10;
    var step = (geoMax - geoMin) / (n - 1);
    return Array.from({length: n}, function(_, i) {
      return Math.round(geoMin + step * i);
    });
  }

  function getColor(feature) {
    var borderType = getBorderType();
    var areaName = feature.properties.area_name;
    var areaLabel = feature.properties.area_level_label;
    var area = areaName + ' ' + areaLabel;

    var geoSums = borderType === 'Province' ? state.geographic_province_sums : state.geographic_district_sums;
    var accessor = borderType === 'Province' ? 'province_name' : 'district_name';

    if (!geoSums) return '#c0c0c0';

    var result = geoSums.find(function(item) { return item[accessor] === area; });
    if (result) {
      var count = result.count;
      for (var i = 0; i < state.geoScale.length; i++) {
        if (count >= state.geoScale[i] && count < state.geoScale[i + 1]) {
          return colorScale[i];
        }
      }
    }
    return '#c0c0c0';
  }

  function addGeoJsonLayer() {
    if (!hasMap || !state.geojson || !state.geoScale || !state.map) return;

    var borderType = getBorderType();
    var borders = ['Country', borderType];
    var geojson = JSON.parse(JSON.stringify(state.geojson));
    geojson.features = state.geojson.features.filter(function(feature) {
      return borders.indexOf(feature.properties.area_level_label) !== -1;
    });

    if (state.layer) state.map.removeLayer(state.layer);

    state.layer = L.geoJson(geojson, {
      style: function(feature) {
        if (feature.properties.area_level_label !== 'Country') {
          var color = getColor(feature);
          return {stroke: true, weight: 1.5, color: color, opacity: 1, fillColor: color, fillOpacity: 0.75};
        }
        return {weight: 2.2, opacity: 1, color: '#666', stroke: true};
      }
    }).addTo(state.map);

    if (!state.hasFit) {
      try {
        state.map.fitBounds(state.layer.getBounds(), {padding: [6, 6]});
        var currentZoom = state.map.getZoom();
        if (typeof currentZoom === 'number') {
          state.map.setZoom(Math.max(currentZoom - 1, 3));
        }
        state.hasFit = true;
      } catch (e) {
        // ignore fit errors for empty bounds
      }
    }
  }

  function initializeMap() {
    if (!hasMap) return Promise.resolve();

    state.map = L.map('regionalMap', {
      zoomControl: false,
      attributionControl: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      dragging: true,
      maxBounds: [
        [-6, 20],
        [-20, 34]
      ]
    }).setView([-13, 27], 6);

    return fetch(geojsonUrl)
      .then(function(res) { return res.json(); })
      .then(function(geojson) {
        state.geojson = geojson;
        return updateMapData();
      })
      .then(function() {
        setTimeout(function() {
          if (state.map) state.map.invalidateSize();
        }, 0);
      });
  }

  function updateMapData() {
    if (!hasMap || !mapUrl) return Promise.resolve();

    var params = getFilterParams();
    var url = mapUrl + (params.toString() ? '?' + params.toString() : '');

    return fetch(url, {method: 'GET'})
      .then(function(res) { return res.json(); })
      .then(function(data) {
        state.geographic_province_sums = data.geographic_province_sums || [];
        state.geographic_district_sums = data.geographic_district_sums || [];
        state.geoScale = computeGeoScale(
          getBorderType() === 'Province' ? state.geographic_province_sums : state.geographic_district_sums
        );
        addGeoJsonLayer();
      });
  }

  function resetAllFilters() {
    var geoSelect = getFilterEl('geographyFilterSelect');
    var suffix = getActiveFilterSuffix();
    var timeAllPreset = getEl('timeAll' + suffix) || getEl('timeAll');
    var startDateInput = getFilterEl('timeStartDate');
    var endDateInput = getFilterEl('timeEndDate');
    var msoSourceSelect = getEl('msoSourceSelect');
    var csaSearchInput = getEl('csaNameSearch');
    var csaSupervisorSearchInput = getEl('csaSupervisorSearch');
    var csaDistrictSearchInput = getEl('csaDistrictSearch');
    var csaWardSearchInput = getEl('csaWardSearch');
    var msoSearchInput = getEl('msoNameSearch');
    var msoProvinceSearchInput = getEl('msoProvinceSearch');

    if (geoSelect) {
      geoSelect.value = '';
      if (geoSelect.value !== '' && geoSelect.options.length > 0) {
        geoSelect.selectedIndex = 0;
      }
    }
    if (timeAllPreset) timeAllPreset.checked = true;
    if (startDateInput) startDateInput.value = '';
    if (endDateInput) endDateInput.value = '';
    if (msoSourceSelect) msoSourceSelect.value = '';
    if (csaSearchInput) csaSearchInput.value = '';
    if (csaSupervisorSearchInput) csaSupervisorSearchInput.value = '';
    if (csaDistrictSearchInput) csaDistrictSearchInput.value = '';
    if (csaWardSearchInput) csaWardSearchInput.value = '';
    if (msoSearchInput) msoSearchInput.value = '';
    if (msoProvinceSearchInput) msoProvinceSearchInput.value = '';

    tableSortState = {
      csa_sort: tableSortDefaults.csa_sort,
      csa_dir: tableSortDefaults.csa_dir,
      csa_search: tableSortDefaults.csa_search,
      csa_supervisor_search: tableSortDefaults.csa_supervisor_search,
      csa_district_search: tableSortDefaults.csa_district_search,
      csa_ward_search: tableSortDefaults.csa_ward_search,
      csa_page: tableSortDefaults.csa_page,
      mso_sort: tableSortDefaults.mso_sort,
      mso_dir: tableSortDefaults.mso_dir,
      mso_search: tableSortDefaults.mso_search,
      mso_province_search: tableSortDefaults.mso_province_search,
      mso_page: tableSortDefaults.mso_page
    };
    focusState.activeCsaSearchId = null;
    focusState.csaCaretPos = null;
    focusState.activeMsoSearchId = null;
    focusState.msoCaretPos = null;
  }

  function wireEvents() {
    var csaSearchTimer = null;
    var msoSearchTimer = null;
    document.addEventListener('pointerdown', function(event) {
      var target = event.target;
      if (!target) return;
      if (isCsaSearchInputId(target.id)) {
        focusState.activeCsaSearchId = target.id;
        focusState.activeMsoSearchId = null;
        focusState.msoCaretPos = null;
        return;
      }
      if (isMsoSearchInputId(target.id)) {
        focusState.activeMsoSearchId = target.id;
        focusState.activeCsaSearchId = null;
        focusState.csaCaretPos = null;
        return;
      }
      focusState.activeCsaSearchId = null;
      focusState.csaCaretPos = null;
      focusState.activeMsoSearchId = null;
      focusState.msoCaretPos = null;
    });

    document.addEventListener('click', function(event) {
      if (event.target && /^regionalFiltersReset/.test(event.target.id || '')) {
        if (csaSearchTimer) clearTimeout(csaSearchTimer);
        if (msoSearchTimer) clearTimeout(msoSearchTimer);
        resetAllFilters();
        updateLabel();
        updateMapData();
        refreshDataComponents({csa: true, mso: true});
        return;
      }

      var link = event.target.closest('.regional-sort-link, .regional-page-link');
      if (!link) return;
      if (link.classList.contains('disabled') || link.getAttribute('aria-disabled') === 'true') return;

      event.preventDefault();
      var href = link.getAttribute('href') || '';
      var table = link.getAttribute('data-table');
      if (!href || !table) return;

      var params = new URLSearchParams(href.replace('?', ''));
      if (table === 'csa') {
        tableSortState.csa_sort = params.get('csa_sort') || tableSortState.csa_sort;
        tableSortState.csa_dir = params.get('csa_dir') || tableSortState.csa_dir;
        tableSortState.csa_page = params.get('csa_page') || tableSortState.csa_page;
        refreshDataComponents({csa: true});
        return;
      }

      if (table === 'mso') {
        tableSortState.mso_sort = params.get('mso_sort') || tableSortState.mso_sort;
        tableSortState.mso_dir = params.get('mso_dir') || tableSortState.mso_dir;
        tableSortState.mso_page = params.get('mso_page') || tableSortState.mso_page;
        refreshDataComponents({mso: true});
      }
    });

    document.addEventListener('change', function(event) {
      var target = event.target;
      if (!target) return;

      var isPrimaryFilter =
        /^geographyFilterSelect/.test(target.id || '') ||
        /^timeStartDate/.test(target.id || '') ||
        /^timeEndDate/.test(target.id || '') ||
        /^timePreset/.test(target.name || '');

      if (isPrimaryFilter) {
        tableSortState.csa_page = '1';
        tableSortState.mso_page = '1';
        updateLabel();
        updateMapData();
        refreshDataComponents({csa: true, mso: true});
        return;
      }

      if (target.id === 'msoSourceSelect') {
        tableSortState.mso_page = '1';
        refreshDataComponents({mso: true});
      }
    });

    document.addEventListener('input', function(event) {
      var target = event.target;
      if (!target) return;
      if (isCsaSearchInputId(target.id)) {
        var csaSearchKey = getCsaSearchStateKey(target.id);
        if (csaSearchKey) tableSortState[csaSearchKey] = (target.value || '').trim();
        tableSortState.csa_page = '1';
        focusState.activeCsaSearchId = target.id;
        focusState.csaCaretPos = target.selectionStart;
        if (csaSearchTimer) clearTimeout(csaSearchTimer);
        csaSearchTimer = setTimeout(function() {
          refreshDataComponents({csa: true});
        }, 1000);
        return;
      }

      if (isMsoSearchInputId(target.id)) {
        var msoSearchKey = getMsoSearchStateKey(target.id);
        if (msoSearchKey) tableSortState[msoSearchKey] = (target.value || '').trim();
        tableSortState.mso_page = '1';
        focusState.activeMsoSearchId = target.id;
        focusState.msoCaretPos = target.selectionStart;
        if (msoSearchTimer) clearTimeout(msoSearchTimer);
        msoSearchTimer = setTimeout(function() {
          refreshDataComponents({mso: true});
        }, 1000);
      }
    });

    window.addEventListener('resize', function() {
      if (state.map) state.map.invalidateSize();
    });
  }

  updateLabel();
  initializeMap();
  wireEvents();

  // Pre-load all four regional-operations slots through DashboardLoader so
  // that:
  //  1. data-loaded="1" is set via setContainerHtml — the same flag that
  //     loadComponentOnce checks before making a network request on tab switch.
  //  2. In-flight deduplication prevents a double-fetch if the user clicks a
  //     tab while the pre-load is still in progress.
  // Filter- or sort-triggered refreshes later use refreshComponent() above
  // (which also keeps data-loaded="1" in sync), so tab switching always
  // remains a CSS show/hide with no extra network requests.
  var loader = window.DashboardLoader;
  if (loader && typeof loader.loadComponentOnce === 'function') {
    var filtersSlot    = getEl('regionalFiltersComponent');
    var msoFiltersSlot = getEl('regionalMsoFiltersComponent');
    var csaSlot        = getEl('regionalCsaComponent');
    var msoSlot        = getEl('regionalMsoComponent');
    if (filtersSlot)    loader.loadComponentOnce(filtersSlot).catch(function() {});
    if (msoFiltersSlot) loader.loadComponentOnce(msoFiltersSlot).catch(function() {});
    if (csaSlot)        loader.loadComponentOnce(csaSlot).catch(function() {});
    if (msoSlot)        loader.loadComponentOnce(msoSlot).catch(function() {});
  }
})();
