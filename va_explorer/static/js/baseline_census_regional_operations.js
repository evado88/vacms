(function() {
  var mapEl = document.getElementById('regionalMap');
  var hasMap = Boolean(mapEl && window.L);

  var componentsEl = document.getElementById('regionalOperationsComponents');
  var componentUrls = {
    filters: componentsEl ? componentsEl.getAttribute('data-filters-url') : '',
    csa: componentsEl ? componentsEl.getAttribute('data-csa-url') : ''
  };
  var csaComponentEl = document.getElementById('regionalCsaComponent');
  var csaSearchInputEl = document.getElementById('csaNameSearch');
  var csaSupervisorSearchInputEl = document.getElementById('csaSupervisorSearch');
  var csaDistrictSearchInputEl = document.getElementById('csaDistrictSearch');
  var csaWardSearchInputEl = document.getElementById('csaWardSearch');
  var focusState = {
    activeCsaSearchId: null,
    csaCaretPos: null
  };
  var tableSortDefaults = {
    csa_sort: 'visits',
    csa_dir: 'desc',
    csa_search: '',
    csa_supervisor_search: '',
    csa_district_search: '',
    csa_ward_search: '',
    csa_page: '1'
  };
  var tableSortState = {
    csa_sort: csaComponentEl ? (csaComponentEl.getAttribute('data-csa-sort') || tableSortDefaults.csa_sort) : tableSortDefaults.csa_sort,
    csa_dir: csaComponentEl ? (csaComponentEl.getAttribute('data-csa-dir') || tableSortDefaults.csa_dir) : tableSortDefaults.csa_dir,
    csa_search: csaSearchInputEl ? (csaSearchInputEl.value || '') : '',
    csa_supervisor_search: csaSupervisorSearchInputEl ? (csaSupervisorSearchInputEl.value || '') : '',
    csa_district_search: csaDistrictSearchInputEl ? (csaDistrictSearchInputEl.value || '') : '',
    csa_ward_search: csaWardSearchInputEl ? (csaWardSearchInputEl.value || '') : '',
    csa_page: csaComponentEl ? (csaComponentEl.getAttribute('data-csa-page') || tableSortDefaults.csa_page) : tableSortDefaults.csa_page
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

  function getEl(id) {
    return document.getElementById(id);
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

  function getSelectedPreset() {
    return document.querySelector('input[name="timePreset"]:checked');
  }

  function getDateRange() {
    var startInput = getEl('timeStartDate');
    var endInput = getEl('timeEndDate');
    var startVal = startInput ? startInput.value : '';
    var endVal = endInput ? endInput.value : '';
    return {start: startVal, end: endVal};
  }

  function getFilterParams() {
    var params = new URLSearchParams();
    var geoSelect = getEl('geographyFilterSelect');
    var csaSearchInput = getEl('csaNameSearch');
    var csaSupervisorSearchInput = getEl('csaSupervisorSearch');
    var csaDistrictSearchInput = getEl('csaDistrictSearch');
    var csaWardSearchInput = getEl('csaWardSearch');
    var selectedPreset = getSelectedPreset();
    var range = getDateRange();

    if (geoSelect && geoSelect.value) params.set('geo', geoSelect.value);
    if (selectedPreset && selectedPreset.id) params.set('time_preset', selectedPreset.id);
    if (range.start) params.set('start_date', range.start);
    if (range.end) params.set('end_date', range.end);
    if (csaSearchInput) tableSortState.csa_search = csaSearchInput.value.trim();
    if (csaSupervisorSearchInput) tableSortState.csa_supervisor_search = csaSupervisorSearchInput.value.trim();
    if (csaDistrictSearchInput) tableSortState.csa_district_search = csaDistrictSearchInput.value.trim();
    if (csaWardSearchInput) tableSortState.csa_ward_search = csaWardSearchInput.value.trim();
    if (tableSortState.csa_sort) params.set('csa_sort', tableSortState.csa_sort);
    if (tableSortState.csa_dir) params.set('csa_dir', tableSortState.csa_dir);
    if (tableSortState.csa_search) params.set('csa_search', tableSortState.csa_search);
    if (tableSortState.csa_supervisor_search) params.set('csa_supervisor_search', tableSortState.csa_supervisor_search);
    if (tableSortState.csa_district_search) params.set('csa_district_search', tableSortState.csa_district_search);
    if (tableSortState.csa_ward_search) params.set('csa_ward_search', tableSortState.csa_ward_search);
    if (tableSortState.csa_page) params.set('csa_page', tableSortState.csa_page);

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
      })
      .catch(function() {
        // Keep last-rendered component if refresh fails.
      });
  }

  function refreshDataComponents(options) {
    var opts = options || {csa: true};
    var requests = [];

    if (opts.filters) {
      requests.push(refreshComponent('regionalFiltersComponent', componentUrls.filters));
    }
    if (opts.csa) {
      requests.push(refreshComponent('regionalCsaComponent', componentUrls.csa));
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
    var presetId = selected && selected.id;
    if (!presetId || !mapping[presetId]) return;
    label.textContent = 'Geography: ' + mapping[presetId];
  }

  function getBorderType() {
    var geoSelect = getEl('geographyFilterSelect');
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
    var geoSelect = getEl('geographyFilterSelect');
    var timeAllPreset = getEl('timeAll');
    var startDateInput = getEl('timeStartDate');
    var endDateInput = getEl('timeEndDate');
    var csaSearchInput = getEl('csaNameSearch');
    var csaSupervisorSearchInput = getEl('csaSupervisorSearch');
    var csaDistrictSearchInput = getEl('csaDistrictSearch');
    var csaWardSearchInput = getEl('csaWardSearch');

    if (geoSelect) {
      geoSelect.value = '';
      if (geoSelect.value !== '' && geoSelect.options.length > 0) {
        geoSelect.selectedIndex = 0;
      }
    }
    if (timeAllPreset) timeAllPreset.checked = true;
    if (startDateInput) startDateInput.value = '';
    if (endDateInput) endDateInput.value = '';
    if (csaSearchInput) csaSearchInput.value = '';
    if (csaSupervisorSearchInput) csaSupervisorSearchInput.value = '';
    if (csaDistrictSearchInput) csaDistrictSearchInput.value = '';
    if (csaWardSearchInput) csaWardSearchInput.value = '';

    tableSortState = {
      csa_sort: tableSortDefaults.csa_sort,
      csa_dir: tableSortDefaults.csa_dir,
      csa_search: tableSortDefaults.csa_search,
      csa_supervisor_search: tableSortDefaults.csa_supervisor_search,
      csa_district_search: tableSortDefaults.csa_district_search,
      csa_ward_search: tableSortDefaults.csa_ward_search,
      csa_page: tableSortDefaults.csa_page
    };
    focusState.activeCsaSearchId = null;
    focusState.csaCaretPos = null;
  }

  function wireEvents() {
    var csaSearchTimer = null;
    document.addEventListener('pointerdown', function(event) {
      var target = event.target;
      if (!target) return;
      if (isCsaSearchInputId(target.id)) {
        focusState.activeCsaSearchId = target.id;
        return;
      }
      focusState.activeCsaSearchId = null;
      focusState.csaCaretPos = null;
    });

    document.addEventListener('click', function(event) {
      if (event.target && /^regionalFiltersReset/.test(event.target.id || '')) {
        if (csaSearchTimer) clearTimeout(csaSearchTimer);
        resetAllFilters();
        updateLabel();
        updateMapData();
        refreshDataComponents({csa: true});
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
        updateLabel();
        updateMapData();
        refreshDataComponents({csa: true});
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
      }
    });

    window.addEventListener('resize', function() {
      if (state.map) state.map.invalidateSize();
    });
  }

  updateLabel();
  initializeMap();
  wireEvents();

  var loader = window.DashboardLoader;
  if (loader && typeof loader.loadComponentOnce === 'function') {
    var filtersSlot = getEl('regionalFiltersComponent');
    var csaSlot     = getEl('regionalCsaComponent');
    if (filtersSlot) loader.loadComponentOnce(filtersSlot).catch(function() {});
    if (csaSlot)     loader.loadComponentOnce(csaSlot).catch(function() {});
  }
})();
