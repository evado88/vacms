  /**
   * Summary. Governs the behavior of the select2 Locations dropdown
   *
   * Examples: User creation and update forms.
   *
   * Note: CSS styling is used to emulate hierarchy due to HTML specification
   * which allows only one level of nesting in optgroup
   * https://select2.org/options#hierarchical-options
   */

$(document).ready(function() {
  /**
   * Summary. Initializes the select2 dropdown
   *          Must explicitly set the width here due to showing/hiding of select2, which
   *          can interfere with the width: https://select2.org/appearance#container-width
   */
  $(".facility-restrictions-select").select2({
    placeholder: "Search for facilities to add to Field Worker's geographic access",
    width:'100%'
  });

  $(".location-restrictions-select").select2({
    placeholder: "Search for location(s) to add to user's geographic access",
    templateResult: formatResult,
    width:'100%'
  });

  /**
   * Summary. Disables the descendant locations, if any, of a location that is selected
   *          Removes any previously-selected descendant locations from the select2 input field
   */
  $(".location-restrictions-select").on('select2:select', function(e) {
    let descendants = e.params.data.element.dataset.descendants;

    if(descendants !== undefined) {
      const descendantsHash = createDescendantsHash(descendants);

      toggleDisabledStateOfDescendants(descendantsHash, true);
      removeSelectedDescendants(descendantsHash);
    }
  });

  /**
   * Summary. Enables the descendant locations, if any, of a location that is unselected
   */
  $(".location-restrictions-select").on('select2:unselect', function(e) {
    let descendants = e.params.data.element.dataset.descendants;

    if(descendants !== undefined) {
      toggleDisabledStateOfDescendants(createDescendantsHash(descendants), false);
    }
  });

  /**
   * Functions to call and variables to define on page load
   *
   */
  const geographicAccessID = 'div_id_geographic_access';
  const locationRestrictionsID= 'div_id_location_restrictions';
  const facilityRestrictionsID = 'div_id_facility_restrictions';

  const locationRestrictionsClass = 'location-restrictions-select';
  const facilityRestrictionsClass = 'facility-restrictions-select';

  updateFormForRoleSelected();
  disableDescendantsForSelectedLocations();

  /**
   * Summary. Applies the relevant CSS style to the location in the select2 dropdown menu, based on depth
   *          in the Locations tree (stored in data-depth attribute on the option element).
   */
  function formatResult(node) {
    let depth = 0;

    if(node.element !== undefined){
      depth = node.element.getAttribute('data-depth');

      if(depth.trim() !== ''){
        depth = parseInt(depth.match(/\d+/)[0]);
      }
    }
    return $('<span class="location-tree-depth-'+depth+'">'+ node.text +'</span>');
  }

  /**
   * Summary. Disables the descendant locations, if any, of a selected location on page load
   *          Necessary for user edit, where locations were previously assigned
   */
  function disableDescendantsForSelectedLocations() {
    $('#id_location_restrictions').select2('data').forEach(function(location) {
      let descendants = location.element.getAttribute("data-descendants");

      // Note: getAttribute returns null if the attribute does not exist
      if(descendants !== null) {
        toggleDisabledStateOfDescendants(createDescendantsHash(descendants), true);
      }
    })
  }

  /**
   * Summary. Toggles the disabled attribute in the option element
   *
   * @param {object}   locationsHash
   * @param {boolean}  disabledState  boolean to indicate if disabled is true or false
   */
  function toggleDisabledStateOfDescendants(locationsHash, disabledState) {
    $(".location-restrictions-select option").filter(function() {
      return this.value in locationsHash;
    }).prop('disabled', disabledState);
  }

  /**
   * Summary. Removes previously selected descendants from the select2 input field when their
   *          parent location is subsequently chosen by the user
   */
  function removeSelectedDescendants(descendantsHash) {
    let updatedLocations = []

    $('#id_location_restrictions').select2('data').forEach(function(element) {
      if(!(element["id"] in descendantsHash)) {
        updatedLocations.push(element["id"]);
      }
    });

    $('#id_location_restrictions').val(updatedLocations).trigger('change');
  }

  /**
   * Summary. Creates a hash (object) to facilitate lookup of descendants
   */
  function createDescendantsHash(descendants) {
    let descendantsHash = {};

    if (descendants !== undefined) {
      descendants = descendants.replace(/'/g, '"')
    }

    JSON.parse(descendants).forEach(function(element) {
      descendantsHash[element] = true;
    });

    return descendantsHash;
  }

  /**
   * Update status of Can View PII checkbox depending on whether selected group can view PII.
   */
   function updatePIICheckbox() {
    const groupCanViewPII = Boolean($('#id_group option:selected').data('view-pii'));
    $('#id_view_pii').attr('disabled', groupCanViewPII);
    $('#id_view_pii').attr('checked', groupCanViewPII);
  }

  // On load, if group can view PII, disable checkbox.
  // Don't DISABLE it if the group CAN'T view PII because the user might have it checked explicitly.
  const groupCanViewPII = Boolean($('#id_group option:selected').data('view-pii'));
  if (groupCanViewPII) {
    $('#id_view_pii').attr('disabled', true);
    $('#id_view_pii').attr('checked', true);
  }

  /**
   * Update status of Can Download Data checkbox depending on whether selected group can download data.
   */
   function updateDownloadDataCheckbox() {
    const groupCanDownloadData = Boolean($('#id_group option:selected').data('download-data'));
    $('#id_download_data').attr('disabled', groupCanDownloadData);
    $('#id_download_data').attr('checked', groupCanDownloadData);
  }

  // On load, if group can view PII, disable checkbox.
  // Don't DISABLE it if the group CAN'T view PII because the user might have it checked explicitly.
  const groupCanDownloadData = Boolean($('#id_group option:selected').data('download-data'));
  if (groupCanDownloadData) {
    $('#id_download_data').attr('disabled', true);
    $('#id_download_data').attr('checked', true);
  }


  /**
   * Summary. Disables all geographic access controls and marks the wrapper visually.
   *          Used when no role is selected — controls remain visible but inactive.
   */
  function disableGeoControls() {
    $('input[name="geographic_access"]').prop('disabled', true);
    $('#id_location_restrictions').prop('disabled', true).trigger('change');
    $('#id_facility_restrictions').prop('disabled', true).trigger('change');
    // Ensure all three remain visible in the disabled state
    $('#' + geographicAccessID).show();
    $('#' + locationRestrictionsID).show();
    $('#' + facilityRestrictionsID).show();
    $('#geo-access-controls').addClass('geo-access-disabled');
  }

  /**
   * Summary. Enables the geographic access radio controls and removes the disabled wrapper style.
   *          Called when a role is selected; role-specific logic then enables/disables
   *          location_restrictions and facility_restrictions individually.
   */
  function enableGeoControls() {
    $('input[name="geographic_access"]').prop('disabled', false);
    $('#geo-access-controls').removeClass('geo-access-disabled');
    $('#' + geographicAccessID).removeClass('field-disabled');
  }

  /**
   * Summary. Disables the location restrictions field and dims it visually.
   */
  function disableLocationRestrictions() {
    $('#id_location_restrictions').prop('disabled', true).trigger('change');
    $('#' + locationRestrictionsID).addClass('field-disabled');
  }

  /**
   * Summary. Enables the location restrictions field and removes the disabled visual style.
   */
  function enableLocationRestrictions() {
    $('#id_location_restrictions').prop('disabled', false).trigger('change');
    $('#' + locationRestrictionsID).removeClass('field-disabled');
  }

  /**
   * Summary. Disables the facility restrictions field and dims it visually.
   */
  function disableFacilityRestrictions() {
    $('#id_facility_restrictions').prop('disabled', true).trigger('change');
    $('#' + facilityRestrictionsID).addClass('field-disabled');
  }

  /**
   * Summary. Enables the facility restrictions field and removes the disabled visual style.
   */
  function enableFacilityRestrictions() {
    $('#id_facility_restrictions').prop('disabled', false).trigger('change');
    $('#' + facilityRestrictionsID).removeClass('field-disabled');
  }

  /**
   * Summary. Updates the form location controls based on the role selected. The controls include:
   *  1. the geographic access radio button
   *  2. the location restrictions select2 dropdown (always visible; enabled/disabled by role)
   *  3. the facility restrictions select2 dropdown (always visible; enabled only for Field Workers)
   */
  function updateFormForRoleSelected() {
    let select2ToClear = [];
    let fieldToClear = [];

    // No Role selected — keep all controls visible but disabled
    if(!$('#id_group option:selected').val()) {
      disableGeoControls();
      select2ToClear.push(locationRestrictionsClass, facilityRestrictionsClass);
    }

    // Role selected is Field Worker
    else if($('#id_group option:selected').text() === "Field Workers") {
      enableGeoControls();
      // Field Workers use facility restrictions, not location restrictions
      disableLocationRestrictions();
      enableFacilityRestrictions();
      select2ToClear.push(locationRestrictionsClass);

      // Show geographic access but disable it (Field Workers are always location-specific)
      $('#' + geographicAccessID).show();
      $('input[name="geographic_access"]').prop('disabled', true);
      $('#' + geographicAccessID).addClass('field-disabled');
      $('#' + locationRestrictionsID).show();
      $('#' + facilityRestrictionsID).show();

      // Add asterisk to facility restrictions select2 indicating it is required
      addRequiredAsterisk();

      // Set to location-specific (locked for Field Workers)
      $("input[name=geographic_access]").val(["location-specific"]);
    }

    // Role selected is not Field Worker (Admin, Data Manager, Data Viewer, etc.)
    else {
      enableGeoControls();
      // Facility restrictions do not apply to this role
      disableFacilityRestrictions();
      select2ToClear.push(facilityRestrictionsClass);

      $('#' + geographicAccessID).show();
      $('#' + locationRestrictionsID).show();
      $('#' + facilityRestrictionsID).show();

      // Admins and Senior Management default to national; all other roles default to location-specific
      const NATIONAL_ACCESS_ROLES = ["Senior Management", "Admins"];
      const role = $('#id_group option:selected').text();
      if (NATIONAL_ACCESS_ROLES.includes(role)) {
        $("input[name=geographic_access]").val(["national"]);
      } else {
        $("input[name=geographic_access]").val(["location-specific"]);
      }

      // This method will determine if locationRestrictions should be enabled/disabled
      updateFormForGeographicAccess();
    }

    // Clear the appropriate elements
    select2ToClear.forEach(select2 => $('.' + select2).val(null).trigger('change'));
    fieldToClear.forEach(elem => $('#' + elem).val(''));

    addRequiredAsterisk();
  }

  /**
   * Summary. Adds a span with an asterisk to facility restrictions and location restrictions
   *          as a visual cue to the user that the field is required
   */
  function addRequiredAsterisk() {
    [
        $("label[for='id_location_restrictions']"),
        $("label[for='id_facility_restrictions']")
    ].forEach(function(elem){
      if(elem && elem.children().length==0) {
        elem.append('<span class="asteriskField">*</span>');
      }
    })
  }

  /**
   * Summary. Enables/disables the location select2 dropdown depending on the
   *          value of the geographic_access radio button. The field is always
   *          visible but disabled when national access is selected.
   */
  function updateFormForGeographicAccess() {
    if($('input[name="geographic_access"]:checked').val() === "national") {
      disableLocationRestrictions();
      $('.' + locationRestrictionsClass).val(null).trigger('change');
    }
    else{
      enableLocationRestrictions();
    }
  }

  /**
   * Summary. Event handler that listens for the change of role assignment
   */
  $('#id_group').change(function() {
    updateFormForRoleSelected();
    updatePIICheckbox();
    updateDownloadDataCheckbox();
  });

  /**
   * Summary. Event handler that listens for the change of geographic access
   *          from location-specific to national
   */
  $('input[type=radio][name=geographic_access]').on('change',function(){
    updateFormForGeographicAccess();
  });
});
