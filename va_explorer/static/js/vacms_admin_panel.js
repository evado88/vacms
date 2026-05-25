(function () {
  "use strict";

  const config = window.VACMS_ADMIN_PANEL || {};
  const runUrl = config.runUrl || "";
  const uploadFileUrl = config.uploadFileUrl || "";
  const validateFileUrl = config.validateFileUrl || "";
  const logsUrl = config.logsUrl || "";
  const csrfTokenUrl = config.csrfTokenUrl || "";
  const alertsUrl = config.alertsUrl || "";
  const alertsLogUrl = config.alertsLogUrl || "";
  const configuredCsrfToken = String(config.csrfToken || "").trim();
  let csrfTokenCache = configuredCsrfToken;

  const root = document.getElementById("vacmsAdminPanelRoot");
  const consoleEl = document.getElementById("vacmsCommandConsole");
  const copyBtn = document.getElementById("vacmsConsoleCopyBtn");
  const clearBtn = document.getElementById("vacmsConsoleClearBtn");
  const searchInput = document.getElementById("vacmsCommandSearch");
  const refreshBtn = document.getElementById("vacmsRefreshCommandsBtn");
  const statusCommand = document.getElementById("vacmsStatusCommand");
  const statusRunAt = document.getElementById("vacmsStatusRunAt");
  const statusDuration = document.getElementById("vacmsStatusDuration");
  const statusBadge = document.getElementById("vacmsStatusBadge");
  const bulkUploadBtn = document.getElementById("vacmsBulkUploadBtn");
  const bulkUploadModal = document.getElementById("vacmsBulkUploadModal");
  const bulkUploadFilesInput = document.getElementById("vacmsBulkUploadFilesInput");
  const bulkOverwriteInput = document.getElementById("vacmsBulkOverwriteInput");
  const bulkUploadSubmitBtn = document.getElementById("vacmsBulkUploadSubmitBtn");
  const bulkUploadSpinner = document.getElementById("vacmsBulkUploadSpinner");
  const bulkUploadError = document.getElementById("vacmsBulkUploadError");
  const bulkUploadSummary = document.getElementById("vacmsBulkUploadSummary");
  const alertsList = document.getElementById("vacmsAlertsList");
  const alertsRefreshBtn = document.getElementById("vacmsAlertsRefreshBtn");
  const alertDetailsModal = document.getElementById("vacmsAlertDetailsModal");
  const alertDetailTitle = document.getElementById("vacmsAlertDetailsModalLabel");
  const alertDetailCategory = document.getElementById("vacmsAlertDetailCategory");
  const alertDetailSeverity = document.getElementById("vacmsAlertDetailSeverity");
  const alertDetailTime = document.getElementById("vacmsAlertDetailTime");
  const alertDetailSummary = document.getElementById("vacmsAlertDetailSummary");
  const alertDetailBody = document.getElementById("vacmsAlertDetailBody");

  const runModal = document.getElementById("vacmsRunModal");
  const runModalLabel = document.getElementById("vacmsRunModalLabel");
  const runModalInputs = document.getElementById("vacmsRunModalInputs");
  const runModalError = document.getElementById("vacmsRunModalError");
  const runSubmitBtn = document.getElementById("vacmsRunModalSubmitBtn");
  const modalSpinner = document.getElementById("vacmsModalSpinner");
  const dangerWrap = document.getElementById("vacmsDangerConfirmWrap");
  const dangerInput = document.getElementById("vacmsDangerConfirmInput");
  const passwordWrap = document.getElementById("vacmsPasswordConfirmWrap");
  const passwordInput = document.getElementById("vacmsPasswordConfirmInput");

  if (!root || !runUrl || !consoleEl) return;

  const registryScript = document.getElementById("vacmsCommandRegistryData");
  const registryById = new Map();
  let activeCommand = null;

  if (registryScript) {
    try {
      const grouped = JSON.parse(registryScript.textContent || "{}");
      Object.keys(grouped).forEach((category) => {
        (grouped[category] || []).forEach((command) => {
          registryById.set(command.key, command);
        });
      });
    } catch (_error) {
      // no-op
    }
  }

  const appendOutput = (text) => {
    consoleEl.textContent += text;
    consoleEl.scrollTop = consoleEl.scrollHeight;
  };

  const isValidCsrfToken = (token) => {
    const value = String(token || "").trim();
    if (!value || value === "NOTPROVIDED") return false;
    return value.length === 32 || value.length === 64;
  };

  const getCookie = (name) => {
    const cookies = String(document.cookie || "").split(";");
    for (const cookie of cookies) {
      const parts = cookie.split("=");
      const key = (parts.shift() || "").trim();
      if (key !== name) continue;
      return decodeURIComponent(parts.join("=") || "");
    }
    return "";
  };

  const getCsrfToken = () => {
    if (isValidCsrfToken(csrfTokenCache)) return csrfTokenCache;
    if (isValidCsrfToken(configuredCsrfToken)) return configuredCsrfToken;

    const inputs = Array.from(document.querySelectorAll("input[name='csrfmiddlewaretoken']"));
    for (const input of inputs) {
      const token = String(input.value || "").trim();
      if (isValidCsrfToken(token)) {
        csrfTokenCache = token;
        return token;
      }
    }

    const cookieToken = getCookie("csrftoken");
    if (isValidCsrfToken(cookieToken)) {
      csrfTokenCache = cookieToken;
      return cookieToken;
    }
    return "";
  };

  const refreshCsrfToken = async () => {
    if (!csrfTokenUrl) return "";
    try {
      const response = await fetch(csrfTokenUrl, {
        method: "GET",
        credentials: "same-origin",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) return "";
      const token = String(payload.csrf_token || "").trim();
      if (isValidCsrfToken(token)) {
        csrfTokenCache = token;
        return token;
      }
      return "";
    } catch (_error) {
      return "";
    }
  };

  const ensureCsrfToken = async () => {
    const existing = getCsrfToken();
    if (isValidCsrfToken(existing)) return existing;
    const refreshed = await refreshCsrfToken();
    if (isValidCsrfToken(refreshed)) return refreshed;
    return "";
  };

  const buildCsrfHeaders = async (extraHeaders = {}) => {
    const token = await ensureCsrfToken();
    if (!isValidCsrfToken(token)) {
      throw new Error("CSRF token unavailable. Refresh the page and try again.");
    }
    return { ...extraHeaders, "X-CSRFToken": token };
  };

  const setRunState = (running) => {
    if (runSubmitBtn) runSubmitBtn.disabled = running;
    if (modalSpinner) modalSpinner.classList.toggle("d-none", !running);
  };
  const setBulkUploadState = (running) => {
    if (bulkUploadSubmitBtn) bulkUploadSubmitBtn.disabled = running;
    if (bulkUploadSpinner) bulkUploadSpinner.classList.toggle("d-none", !running);
  };

  const logAlert = async ({ category, severity, title, summary = "", details = "", context = {} }) => {
    if (!alertsLogUrl) return;
    try {
      const headers = await buildCsrfHeaders({ "Content-Type": "application/json" });
      await fetch(alertsLogUrl, {
        method: "POST",
        headers,
        credentials: "same-origin",
        body: JSON.stringify({ category, severity, title, summary, details, context }),
      });
    } catch (_error) {
      // no-op
    }
  };

  const attachAlertItemListeners = () => {
    if (!alertsList) return;
    alertsList.querySelectorAll(".vacms-alert-item").forEach((item) => {
      item.addEventListener("click", () => {
        if (alertDetailTitle) alertDetailTitle.textContent = item.dataset.alertTitle || "Alert Details";
        if (alertDetailCategory) alertDetailCategory.textContent = item.dataset.alertCategory || "-";
        if (alertDetailSeverity) alertDetailSeverity.textContent = item.dataset.alertSeverity || "-";
        if (alertDetailTime) alertDetailTime.textContent = item.dataset.alertCreatedAt || "-";
        if (alertDetailSummary) alertDetailSummary.textContent = item.dataset.alertSummary || "-";
        if (alertDetailBody) alertDetailBody.textContent = item.dataset.alertDetails || "No additional details.";
        if (window.jQuery && window.jQuery.fn && window.jQuery(alertDetailsModal).modal) {
          window.jQuery(alertDetailsModal).modal("show");
        }
      });
    });
  };

  const renderAlerts = (alerts) => {
    if (!alertsList) return;
    alertsList.innerHTML = "";
    if (!alerts || !alerts.length) {
      const emptyState = document.createElement("div");
      emptyState.className = "small text-muted";
      emptyState.textContent = "No alerts yet.";
      alertsList.appendChild(emptyState);
      return;
    }
    alerts.forEach((alert) => {
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = `btn btn-sm vacms-alert-tag ${alert.color_class || "alert-tag-yellow"} mr-1 mb-2 vacms-alert-item`;
      tag.textContent = alert.title || "Alert";
      tag.dataset.alertTitle = alert.title || "Alert";
      tag.dataset.alertSummary = alert.summary || "";
      tag.dataset.alertDetails = alert.details || "";
      tag.dataset.alertCategory = alert.category || "-";
      tag.dataset.alertSeverity = alert.severity || "-";
      tag.dataset.alertCreatedAt = alert.created_at || "-";
      alertsList.appendChild(tag);
    });
    attachAlertItemListeners();
  };

  const refreshAlerts = async () => {
    if (!alertsUrl) return;
    try {
      const response = await fetch(alertsUrl, {
        method: "GET",
        credentials: "same-origin",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) return;
      renderAlerts(payload.alerts || []);
    } catch (_error) {
      // no-op
    }
  };

  const setStatus = (commandId, payload) => {
    if (statusCommand) statusCommand.textContent = commandId || "N/A";
    if (statusRunAt) statusRunAt.textContent = payload?.finished_at || "N/A";
    if (statusDuration) statusDuration.textContent = payload?.duration_ms != null ? `${payload.duration_ms} ms` : "N/A";
    if (statusBadge) {
      statusBadge.className = "badge";
      if (!payload) {
        statusBadge.classList.add("badge-light", "border");
        statusBadge.textContent = "No runs";
        return;
      }
      if (payload.ok) {
        statusBadge.classList.add("badge-success");
        statusBadge.textContent = "Success";
      } else {
        statusBadge.classList.add("badge-danger");
        statusBadge.textContent = "Failed";
      }
    }
  };

  const sessionKey = (commandId, inputName) => `adminpanel:${commandId}:${inputName}`;

  const createInputRow = (commandId, input) => {
    const wrap = document.createElement("div");
    wrap.className = "form-group";

    const label = document.createElement("label");
    label.className = "small font-weight-bold";
    label.textContent = input.name;
    wrap.appendChild(label);

    const cachedValue = window.sessionStorage.getItem(sessionKey(commandId, input.name)) || "";

    if (input.type === "bool") {
      const div = document.createElement("div");
      div.className = "custom-control custom-checkbox";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "custom-control-input run-input";
      checkbox.id = `run-${commandId}-${input.name}`;
      checkbox.name = input.name;
      checkbox.checked = cachedValue === "true" || String(input.default).toLowerCase() === "true";
      const checkboxLabel = document.createElement("label");
      checkboxLabel.className = "custom-control-label";
      checkboxLabel.htmlFor = checkbox.id;
      checkboxLabel.textContent = input.help || "Enable";
      div.appendChild(checkbox);
      div.appendChild(checkboxLabel);
      wrap.appendChild(div);
      return wrap;
    }

    let control;
    if ((input.choices || []).length) {
      control = document.createElement("select");
      const emptyOption = document.createElement("option");
      emptyOption.value = "";
      emptyOption.textContent = "Select...";
      control.appendChild(emptyOption);
      (input.choices || []).forEach((choice) => {
        const opt = document.createElement("option");
        opt.value = choice;
        opt.textContent = choice;
        control.appendChild(opt);
      });
      if (cachedValue) control.value = cachedValue;
    } else {
      control = document.createElement("input");
      control.type = input.type === "int" ? "number" : "text";
      control.value = cachedValue || input.default || "";
      control.placeholder = input.standardized_name || input.filename_pattern || input.help || "";
    }
    control.className = "form-control form-control-sm run-input";
    control.name = input.name;
    control.dataset.inputType = input.type || "";
    if (input.required) control.required = true;
    wrap.appendChild(control);

    if (input.type === "file") {
      const hint = document.createElement("small");
      hint.className = "form-text text-muted";
      hint.textContent = `Standardized filename: ${input.standardized_name || input.filename_pattern || "DATA_FILE.csv"}`;
      wrap.appendChild(hint);

      const uploadControls = document.createElement("div");
      uploadControls.className = "mt-2";

      const pickBtn = document.createElement("button");
      pickBtn.type = "button";
      pickBtn.className = "btn btn-outline-primary btn-sm";
      pickBtn.textContent = "Select file to upload";
      uploadControls.appendChild(pickBtn);

      const filePicker = document.createElement("input");
      filePicker.type = "file";
      filePicker.className = "d-none";
      filePicker.accept = ".csv,text/csv";
      uploadControls.appendChild(filePicker);

      const uploadResult = document.createElement("span");
      uploadResult.className = "small ml-2 text-muted";
      uploadControls.appendChild(uploadResult);
      wrap.appendChild(uploadControls);

      if (uploadFileUrl) {
        pickBtn.addEventListener("click", () => filePicker.click());
        filePicker.addEventListener("change", async () => {
          const selected = filePicker.files && filePicker.files[0] ? filePicker.files[0] : null;
          if (!selected) return;
          const targetFilename = String(control.value || selected.name || "").trim();
          if (!targetFilename) {
            uploadResult.textContent = "No filename selected";
            uploadResult.classList.remove("text-success");
            uploadResult.classList.add("text-danger");
            return;
          }
          uploadResult.textContent = "Uploading...";
          uploadResult.classList.remove("text-success", "text-danger");
          const formData = new FormData();
          formData.append("file", selected);
          formData.append("filename", targetFilename);

          try {
            const headers = await buildCsrfHeaders();
            const response = await fetch(uploadFileUrl, {
              method: "POST",
              headers,
              credentials: "same-origin",
              body: formData,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
              uploadResult.textContent = payload.error || "Upload failed";
              uploadResult.classList.add("text-danger");
              return;
            }
            control.value = payload.filename || targetFilename;
            uploadResult.textContent = payload.saved_to
              ? `Saved to: ${payload.saved_to}`
              : "Uploaded";
            uploadResult.title = payload.saved_to || "";
            uploadResult.classList.remove("text-danger");
            uploadResult.classList.add("text-success");
          } catch (_error) {
            uploadResult.textContent = "Upload failed";
            uploadResult.classList.add("text-danger");
          } finally {
            filePicker.value = "";
          }
        });
      } else {
        pickBtn.disabled = true;
      }

      if (validateFileUrl) {
        const validateBtn = document.createElement("button");
        validateBtn.type = "button";
        validateBtn.className = "btn btn-outline-secondary btn-sm mt-2";
        validateBtn.textContent = "Validate file exists";
        const result = document.createElement("span");
        result.className = "small ml-2 text-muted";
        validateBtn.addEventListener("click", async () => {
          result.textContent = "Checking...";
          try {
            const headers = await buildCsrfHeaders({ "Content-Type": "application/json" });
            const response = await fetch(validateFileUrl, {
              method: "POST",
              headers,
              credentials: "same-origin",
              body: JSON.stringify({ filename: String(control.value || "").trim() }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
              result.textContent = payload.error || "Invalid file";
              result.classList.remove("text-success");
              result.classList.add("text-danger");
              return;
            }
            if (payload.exists) {
              result.textContent = "Found";
              result.classList.remove("text-danger");
              result.classList.add("text-success");
            } else {
              result.textContent = "Not found";
              result.classList.remove("text-success");
              result.classList.add("text-danger");
            }
          } catch (_error) {
            result.textContent = "Check failed";
            result.classList.remove("text-success");
            result.classList.add("text-danger");
          }
        });
        wrap.appendChild(validateBtn);
        wrap.appendChild(result);
      }
    } else if (input.help) {
      const help = document.createElement("small");
      help.className = "form-text text-muted";
      help.textContent = input.help;
      wrap.appendChild(help);
    }

    return wrap;
  };

  const openRunModal = (command) => {
    activeCommand = command;
    runModalInputs.innerHTML = "";
    runModalError.classList.add("d-none");
    runModalError.textContent = "";
    if (runModalLabel) runModalLabel.textContent = `Run Command: ${command.key}`;

    (command.inputs || []).forEach((input) => {
      runModalInputs.appendChild(createInputRow(command.key, input));
    });

    if (command.dangerous) {
      dangerWrap.classList.remove("d-none");
      dangerInput.value = "";
    } else {
      dangerWrap.classList.add("d-none");
      dangerInput.value = "";
    }

    // Database-altering, non-favorite commands require password confirmation.
    if (passwordWrap && passwordInput) {
      passwordInput.value = "";
      passwordWrap.classList.toggle("d-none", !command.requires_password);
    }

    if (window.jQuery && window.jQuery.fn && window.jQuery(runModal).modal) {
      window.jQuery(runModal).modal("show");
    }
    logAlert({
      category: "interaction",
      severity: 1,
      title: `Opened command modal: ${command.key}`,
      summary: "User opened command configuration modal.",
      context: { command_id: command.key },
    });
  };

  const collectModalInputs = () => {
    const inputs = {};
    const fields = Array.from(runModalInputs.querySelectorAll(".run-input"));

    fields.forEach((field) => {
      const key = field.name;
      if (!key) return;
      if (field.type === "checkbox") {
        inputs[key] = field.checked;
        window.sessionStorage.setItem(sessionKey(activeCommand.key, key), String(field.checked));
        return;
      }
      const value = String(field.value || "").trim();
      if (value) {
        inputs[key] = value;
        window.sessionStorage.setItem(sessionKey(activeCommand.key, key), value);
      }
      if (field.required && !value) {
        throw new Error(`Missing required input: ${key}`);
      }
    });
    return inputs;
  };

  const runCommand = async () => {
    if (!activeCommand) return;
    if (activeCommand.dangerous && String(dangerInput.value || "").trim() !== "RUN") {
      runModalError.textContent = 'Dangerous command requires confirmation. Type "RUN".';
      runModalError.classList.remove("d-none");
      return;
    }

    const password = passwordInput ? String(passwordInput.value || "") : "";
    if (activeCommand.requires_password && !password) {
      runModalError.textContent = "Confirm your password to run this command.";
      runModalError.classList.remove("d-none");
      return;
    }

    try {
      const inputs = collectModalInputs();
      runModalError.classList.add("d-none");
      runModalError.textContent = "";
      setRunState(true);

      // Password (when required) is sent only on this request and never cached.
      const body = { command_id: activeCommand.key, inputs };
      if (activeCommand.requires_password) body.password = password;

      appendOutput(`\n>>> Running: ${activeCommand.key}\n`);
      const headers = await buildCsrfHeaders({ "Content-Type": "application/json" });
      const response = await fetch(runUrl, {
        method: "POST",
        headers,
        credentials: "same-origin",
        body: JSON.stringify(body),
      });
      if (passwordInput) passwordInput.value = "";

      const payload = await response.json().catch(() => ({}));
      setStatus(activeCommand.key, payload);

      if (!response.ok || !payload.ok) {
        const errorText = payload.output || payload.error || "Command failed";
        appendOutput(`[error] ${errorText}\n`);
        runModalError.textContent = errorText;
        runModalError.classList.remove("d-none");
        await logAlert({
          category: "system",
          severity: 4,
          title: `Command failed: ${activeCommand.key}`,
          summary: errorText,
          details: errorText,
          context: { command_id: activeCommand.key },
        });
        await refreshAlerts();
        return;
      }

      appendOutput(`[ok] duration=${payload.duration_ms}ms start=${payload.started_at} end=${payload.finished_at}\n`);
      appendOutput(`${payload.output || ""}\n`);
      await refreshAlerts();

      if (window.jQuery && window.jQuery.fn && window.jQuery(runModal).modal) {
        window.jQuery(runModal).modal("hide");
      }
    } catch (error) {
      const errorText = error && error.message ? error.message : String(error);
      appendOutput(`[error] ${errorText}\n`);
      runModalError.textContent = errorText;
      runModalError.classList.remove("d-none");
      setStatus(activeCommand ? activeCommand.key : "N/A", { ok: false });
      await logAlert({
        category: "system",
        severity: 3,
        title: "Command execution UI error",
        summary: errorText,
        details: errorText,
      });
      await refreshAlerts();
    } finally {
      setRunState(false);
    }
  };

  const applySearch = () => {
    const query = String(searchInput.value || "").trim().toLowerCase();
    const panels = Array.from(document.querySelectorAll("#vacmsCommandTabsContent .tab-pane"));
    let firstMatchingPanelId = "";

    panels.forEach((panel) => {
      const cards = Array.from(panel.querySelectorAll(".vacms-command-card-wrap"));
      let visibleCount = 0;

      cards.forEach((item) => {
        const haystack = String(item.dataset.search || "").toLowerCase();
        const hidden = !!query && !haystack.includes(query);
        item.classList.toggle("d-none", hidden);
        if (!hidden) visibleCount += 1;
      });

      panel.querySelectorAll(".vacms-danger-zone").forEach((zone) => {
        const hasVisibleItems = !!zone.querySelector(".vacms-command-card-wrap:not(.d-none)");
        zone.classList.toggle("d-none", !!query && !hasVisibleItems);
      });

      if (query && !firstMatchingPanelId && visibleCount > 0) {
        firstMatchingPanelId = panel.id;
      }
    });

    if (query && firstMatchingPanelId) {
      const selector = `[data-toggle="tab"][href="#${firstMatchingPanelId}"]`;
      const firstMatchingTab = document.querySelector(selector);
      if (firstMatchingTab && window.jQuery && window.jQuery.fn && window.jQuery(firstMatchingTab).tab) {
        window.jQuery(firstMatchingTab).tab("show");
      }
    }
  };

  document.querySelectorAll(".vacms-run-command-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const commandId = button.dataset.commandId || "";
      const command = registryById.get(commandId);
      if (!command) return;
      openRunModal(command);
    });
  });
  attachAlertItemListeners();

  if (runSubmitBtn) {
    runSubmitBtn.addEventListener("click", runCommand);
  }

  if (searchInput) {
    searchInput.addEventListener("input", applySearch);
  }

  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      await logAlert({
        category: "interaction",
        severity: 1,
        title: "Refreshed admin panel",
        summary: "User refreshed command list.",
      });
      window.location.reload();
    });
  }

  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(consoleEl.textContent || "");
      } catch (_error) {
        // no-op
      }
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      consoleEl.textContent = "";
      setStatus("N/A", null);
    });
  }

  const runBulkUpload = async () => {
    if (!uploadFileUrl || !bulkUploadFilesInput) return;
    const files = Array.from(bulkUploadFilesInput.files || []);
    if (!files.length) {
      if (bulkUploadError) {
        bulkUploadError.textContent = "Select at least one file.";
        bulkUploadError.classList.remove("d-none");
      }
      return;
    }

    if (bulkUploadError) {
      bulkUploadError.classList.add("d-none");
      bulkUploadError.textContent = "";
    }
    if (bulkUploadSummary) {
      bulkUploadSummary.textContent = `Uploading ${files.length} file(s)...`;
    }

    setBulkUploadState(true);
    let successCount = 0;
    let failCount = 0;
    const overwrite = !bulkOverwriteInput || bulkOverwriteInput.checked;

    for (const file of files) {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("filename", file.name);
      formData.append("overwrite", overwrite ? "true" : "false");

      try {
        const headers = await buildCsrfHeaders();
        const response = await fetch(uploadFileUrl, {
          method: "POST",
          headers,
          credentials: "same-origin",
          body: formData,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) {
          failCount += 1;
          appendOutput(`[upload:error] ${file.name}: ${payload.error || "Upload failed"}\n`);
          await logAlert({
            category: "system",
            severity: 3,
            title: `File upload failed: ${file.name}`,
            summary: payload.error || "Upload failed",
          });
        } else {
          successCount += 1;
          appendOutput(`[upload:ok] ${payload.filename || file.name} (${payload.size_bytes || 0} bytes)\n`);
        }
      } catch (_error) {
        failCount += 1;
        appendOutput(`[upload:error] ${file.name}: Upload failed\n`);
        await logAlert({
          category: "system",
          severity: 3,
          title: `File upload failed: ${file.name}`,
          summary: "Upload failed.",
        });
      }
    }

    if (bulkUploadSummary) {
      bulkUploadSummary.textContent = `Completed. Uploaded: ${successCount}, Failed: ${failCount}.`;
    }
    await refreshAlerts();
    setBulkUploadState(false);
  };

  if (bulkUploadBtn && bulkUploadModal) {
    bulkUploadBtn.addEventListener("click", () => {
      if (bulkUploadError) {
        bulkUploadError.classList.add("d-none");
        bulkUploadError.textContent = "";
      }
      if (bulkUploadSummary) {
        bulkUploadSummary.textContent = "";
      }
      if (bulkUploadFilesInput) {
        bulkUploadFilesInput.value = "";
      }
      if (bulkOverwriteInput) {
        bulkOverwriteInput.checked = true;
      }
      if (window.jQuery && window.jQuery.fn && window.jQuery(bulkUploadModal).modal) {
        window.jQuery(bulkUploadModal).modal("show");
      }
    });
  }

  if (bulkUploadSubmitBtn) {
    bulkUploadSubmitBtn.addEventListener("click", runBulkUpload);
  }

  if (alertsRefreshBtn) {
    alertsRefreshBtn.addEventListener("click", refreshAlerts);
  }

  if (alertsUrl) {
    refreshAlerts();
  }

  // Warm up a valid CSRF token early to avoid first-action failures.
  ensureCsrfToken();

  window.addEventListener("error", (event) => {
    logAlert({
      category: "system",
      severity: 3,
      title: "Client-side JavaScript error",
      summary: event.message || "Unhandled JavaScript error.",
      details: `${event.filename || ""}:${event.lineno || ""}:${event.colno || ""}`,
    });
  });

  window.addEventListener("unhandledrejection", (event) => {
    logAlert({
      category: "system",
      severity: 3,
      title: "Unhandled promise rejection",
      summary: "A client-side promise rejection was not handled.",
      details: String(event.reason || ""),
    });
  });

  if (logsUrl && logsUrl.length) {
    appendOutput(`[info] Logs available at ${logsUrl}\n`);
  }
})();
