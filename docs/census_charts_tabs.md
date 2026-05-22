```text
Create a VACMS system audit trail module that provides a system management audit log similar to the Server Audit Logs interface described below.

Context:
VACMS needs an administrative audit trail for tracking significant system actions performed by users. The audit trail should support accountability, troubleshooting, security monitoring, operational oversight, and administrative review.

The functionality should be added as a tab within the existing VACMS Admin Panel. Reuse the existing admin panel layout, styling, permission structure, navigation patterns, and frontend/backend conventions as much as possible.

Reference UI behaviour:
- Add a new tab in the Admin Panel called “System Audit Trail” or “Audit Logs”.
- The tab should display a Server Audit Logs-style interface.
- Page/section title: System Audit Trail or Server Audit Logs.
- Introductory text should explain that this page contains significant system actions, including changes to users, permissions, records, imports, exports, downloads, form/configuration changes, authentication events, and system settings.
- Filters should include:
  - Action/Type dropdown, defaulting to “All Actions”
  - Date range filter
  - Optional initiator/user filter
  - Optional target/module filter
  - Optional IP address filter
  - Optional status filter
  - Optional keyword search
- Audit log table columns:
  - Time
  - Type
  - Initiator
  - Target
  - Details
  - Status
  - IP Address
- Add a “Download CSV” button above the audit log table, preferably on the far right of the filter/action row.
- The CSV download should export audit log records according to the currently applied filters, unless the existing VACMS convention for downloads is different.
- The CSV file should include useful fields such as timestamp, action type, category, initiator, target, module, details, status, IP address, request path, request method, and user agent where appropriate.
- Ensure CSV download is permission-protected and only available to users allowed to view/export audit logs.

Example action types:
- User › Log in
- User › Log out
- User › Failed login
- User › Permission changed
- User › Role changed
- User › Profile updated
- Data › Create record
- Data › Update record
- Data › Delete record
- Data › Import data
- Data › Export/download data
- Form › Publish draft
- Form › Create or update draft
- Form › Download submissions
- System › Settings changed
- System › Background task started
- System › Background task completed
- System › Background task failed

Implementation requirements:
1. First inspect the existing VACMS codebase for any current audit, logging, activity tracking, admin logging, user action history, import/export logging, download logging, Celery task logging, or Django admin log implementation.
2. Reuse existing implementation as much as possible.
3. Modify, extend, or refactor existing implementation only where necessary to support a robust, maintainable, secure, and performant audit trail.
4. Do not duplicate functionality unnecessarily.
5. Preserve existing behaviour unless it is clearly incorrect, insecure, inefficient, or inconsistent with the new audit log requirements.
6. Follow existing VACMS project conventions for models, views, templates, URLs, permissions, JavaScript, services, and styling.
7. Add the audit log as a tab within the existing Admin Panel rather than creating a completely disconnected standalone interface, unless the current project structure makes a separate route necessary.

Suggested backend implementation:
- Add or enhance an audit log model, for example `SystemAuditLog`, with fields such as:
  - `id`
  - `created_at`
  - `action_type`
  - `action_category`
  - `initiator`
  - `initiator_name`
  - `target_type`
  - `target_id`
  - `target_display`
  - `module`
  - `details`
  - `metadata`
  - `ip_address`
  - `user_agent`
  - `request_path`
  - `request_method`
  - `status`
  - `error_message`
- Use appropriate database indexes for performance, especially on:
  - `created_at`
  - `action_type`
  - `action_category`
  - `initiator`
  - `module`
  - `status`
- Store flexible details in a JSON field where supported.
- Ensure large metadata fields are not rendered directly in full in the table; truncate or collapse details in the UI while preserving the full data for a detail view, modal, or expandable section.
- Implement a reusable audit logging service/helper so other VACMS modules can log actions consistently.
- Where possible, add middleware or signal-based logging for common events such as login, logout, failed login, user updates, permission changes, and sensitive CRUD operations.
- For imports, exports, downloads, and background tasks, add explicit audit logging at the point where the action occurs.
- Ensure audit logging failures do not break the main user workflow. Log audit failures safely to the application logger.

CSV download requirements:
- Add a backend endpoint/view for CSV export.
- The CSV export must respect access permissions.
- The CSV export should apply the same filters currently used on the audit log table.
- Use streaming responses or efficient queryset iteration if the dataset may be large.
- Avoid loading all audit records into memory before writing the CSV.
- Use clear CSV column headers.
- Use safe formatting for JSON/details fields.
- Mask or exclude sensitive values such as passwords, tokens, secrets, session IDs, authentication headers, and other confidential data.
- Generate a meaningful filename, for example:
  - `vacms_audit_logs_YYYYMMDD.csv`
  - or include the selected date range if available.

Suggested frontend/UI implementation:
- Add the audit trail as a tab in the existing Admin Panel.
- Reuse the existing admin panel tab design and layout.
- Display audit records in a clean table using existing VACMS styling.
- Include filters for date range and action type at minimum.
- Add optional filters for initiator, module/target, status, IP address, and keyword search if feasible.
- Add the “Download CSV” button above the table, aligned to the right of the filter row.
- Use pagination or server-side DataTables-style loading for performance.
- Avoid loading all audit records into the browser at once.
- Make long details horizontally scrollable, truncated, or expandable, similar to the reference Server Audit Logs interface.
- Use readable labels for action types, for example `User › Log in` instead of raw enum values.
- Ensure timestamps are displayed in the local/project timezone and formatted consistently with the rest of VACMS.

Security and permissions:
- Restrict access to the audit trail tab to appropriate administrative users only.
- Reuse existing VACMS permission/profile logic where available.
- Add a specific permission such as `view_system_audit_log` if appropriate.
- Add a separate permission such as `export_system_audit_log` if VACMS uses separate export/download permissions.
- Audit log records should be read-only from the UI.
- Do not expose sensitive information such as passwords, tokens, secrets, session IDs, or full authentication headers.
- Mask or omit sensitive fields from metadata.
- Ensure PII handling follows the existing VACMS permission model.

Performance requirements:
- Use queryset filtering, pagination, `select_related`, and indexed fields.
- Avoid expensive JSON parsing in templates.
- Avoid rendering excessive details in the main table.
- Use server-side filtering instead of client-side filtering for large logs.
- Ensure the page remains responsive even with a large number of audit records.
- Ensure CSV download is efficient for large datasets.
- Consider retention policy support or management command hooks if the project already uses scheduled cleanup jobs.

Testing requirements:
- Add or update tests for:
  - Audit log model creation
  - Login/logout/failed login audit entries
  - User and permission change audit entries
  - CRUD audit entries for selected sensitive VACMS records
  - Import/export/download audit entries where applicable
  - Permission restriction for viewing audit logs
  - Permission restriction for downloading CSV
  - Filtering by date range and action type
  - Pagination behaviour
  - CSV export with and without filters
  - CSV export excluding or masking sensitive values
- Ensure existing tests continue to pass.

Deliverables:
- Model/migration changes if required.
- Reusable audit logging utility/service.
- Middleware/signals/hooks where appropriate.
- Views/API endpoints for listing audit records.
- CSV export endpoint.
- Admin Panel tab integration.
- Template/UI for the audit trail table and filters.
- URL routing updates.
- Permission updates.
- Tests.
- Any required documentation or inline comments for developers.

Implementation guidance:
- Prefer small, focused changes.
- Reuse existing VACMS patterns and components.
- Keep the audit logging API simple and reusable.
- Avoid hardcoding module-specific logic where a generic helper can be used.
- Where the project already has logging/history functionality, enhance it rather than replacing it.
- Where audit coverage is too broad for one implementation pass, implement the reusable foundation first and cover the highest-value events: authentication, user/permission changes, data imports, data exports/downloads, and sensitive record create/update/delete actions.
```
