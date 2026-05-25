Below is a Codex/Claude-ready prompt for VACMS:

```text
You are working on the VACMS codebase. Update and improve the current Admin Panel command execution functionality.

Context:
The current Admin Panel implementation displays a list of available VACMS management commands. However, most, if not all, commands currently fail when executed from within the Admin Panel. The implementation needs to be corrected, hardened, and streamlined so that approved commands can be safely executed through the Admin Panel.

Required implementation:

1. Fix Admin Panel command execution
- Review the existing Admin Panel command execution implementation.
- Reuse the existing implementation as much as possible.
- Refactor, enhance, or replace only where necessary to ensure commands execute reliably from within the Admin Panel.
- Ensure commands are executed using Django best practices, preferably through Django management command utilities rather than fragile shell execution.
- Capture and display command output, errors, status, start time, end time, duration, and execution result.
- Ensure command execution failures are handled gracefully and reported clearly to the admin user.
- Prevent unsafe arbitrary command execution.

2. Support manual file uploads for command inputs
- Add functionality that allows an admin user to upload files from the host computer using a file picker/dialog in the Admin Panel.
- Uploaded files must be saved to:

  /srv/vacms/shared/media/uploads/manual/

- The directory must be created automatically if it does not already exist.
- Use Django storage/media best practices and avoid hardcoding paths in multiple places.
- Ensure the upload directory works correctly in production and local/development environments.
- Validate uploaded files before saving.
- Restrict uploads to appropriate file types used by the relevant commands, especially CSV/XLSX where applicable.
- Prevent unsafe filenames and path traversal.
- Preserve the original filename where safe, but ensure naming collisions are handled safely.
- Display the final saved file path to the admin user after upload.

3. Define standard expected upload filenames per command
Each command that requires a manually uploaded file must clearly display the standard filename or expected file naming convention it will use during execution.

For example:
- load_pregnancy_csv should indicate the standard pregnancy CSV filename or expected file input.
- load_pregnancy_outcome_csv should indicate the standard pregnancy outcome CSV filename or expected file input.
- load_death_csv should indicate the standard deaths CSV filename or expected file input.
- load_va_csv should indicate the standard verbal autopsy CSV filename or expected file input.
- load_csa_tracker should indicate the standard CSA tracker CSV filename or expected file input.

Where the command accepts a file path argument, ensure the uploaded file can be selected and passed correctly to the command during execution.

4. Streamline command categorisation
- Review the Admin Panel command list and categories.
- Ensure each command appears under one category only.
- Commands listed under “Favorites” must not appear again under any other category.
- Remove duplicate command entries from non-favorite categories where applicable.
- Ensure category naming and ordering are clear, consistent, and maintainable.

5. Favorites category
The Favorites category must include, at minimum, the following commands:

- load_pregnancy_csv
- load_pregnancy_outcome_csv
- load_death_csv
- load_va_csv
- run_coding_algorithms
- load_csa_tracker

Note:
If load_va_csv is currently duplicated in the implementation or configuration, keep only one instance.

6. Password confirmation for higher-risk commands
- For database-altering commands outside the Favorites list, require the logged-in admin user to confirm their password before execution.
- The password prompt must verify the currently logged-in admin user’s password.
- Do not store or log the password.
- Do not expose the password in request logs, command logs, browser history, or frontend state beyond the immediate verification request.
- If password verification fails, block execution and show a clear error message.
- Favorites commands should remain executable without this additional password prompt unless the existing security design requires otherwise.

7. Safety and permissions
- Only authenticated admin/staff users with the appropriate permissions should access the command execution panel.
- Add or reuse a specific permission for executing admin commands if one already exists.
- Ensure dangerous commands cannot be added or executed accidentally.
- Maintain an allowlist of executable commands rather than exposing all Django management commands by default.
- Do not allow arbitrary shell arguments.
- Validate and sanitize all command parameters.
- Add CSRF protection and ensure all command execution actions are POST-only.
- Add audit logging for command execution attempts, including:
  - user
  - command name
  - parameters excluding sensitive values
  - uploaded file used, where applicable
  - execution status
  - timestamp
  - duration
  - error message, where applicable

8. User interface requirements
- Improve the Admin Panel UI so that each command has:
  - command name
  - category
  - short description
  - required input file, if applicable
  - standard expected filename or naming convention
  - upload/select file control, if applicable
  - execute button
  - execution status/result display
- Show clear feedback when:
  - upload succeeds
  - upload fails
  - command starts
  - command completes successfully
  - command fails
  - password confirmation is required
  - password confirmation fails
- Avoid page crashes or unhandled exceptions.

9. Best practices
- Follow existing VACMS coding patterns and project structure.
- Reuse existing admin panel, logging, permission, and command execution code where possible.
- Keep the implementation modular and maintainable.
- Avoid duplicating command metadata across multiple files if a central registry/configuration can be used.
- Add meaningful comments only where they clarify non-obvious logic.
- Ensure compatibility with the production deployment structure used by VACMS.
- Do not introduce broad or unrelated changes.

10. Testing and verification
After implementation, verify the following:
- The Admin Panel loads successfully.
- Favorites commands appear only under Favorites.
- No command appears in more than one category.
- Manual upload directory is created automatically if missing.
- Files can be uploaded successfully to /srv/vacms/shared/media/uploads/manual/.
- Uploaded files can be passed into applicable management commands.
- Favorite commands execute successfully from the Admin Panel.
- Database-altering non-favorite commands require logged-in admin password confirmation.
- Incorrect password blocks command execution.
- Command output and errors are captured and displayed.
- Command execution attempts are audit logged.
- Existing command-line execution of management commands remains unaffected.

Deliverables:
- Updated Admin Panel command execution implementation.
- Updated command registry/category configuration.
- Manual upload support for command input files.
- Password confirmation workflow for higher-risk non-favorite database-altering commands.
- Audit logging for command execution.
- Any required tests or documented verification steps.
```
