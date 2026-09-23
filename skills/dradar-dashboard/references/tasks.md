# Task execution and submission

The wrapper owns the user workflow; official DRadar owns claiming, durable artifacts, uploading, and the server-side grading contract. Do not fabricate a submission or reimplement upload by posting arbitrary patches to the website API.

## Inspect first

Use the wrapper's `local status --json`, `tasks --json` and `progress --json` for the exact run. Supply the run code via `DRADAR_DASHBOARD_PLAN_CODE`, not a published command or report. Use `--plan-id` only for a verified local plan. Read current `dradar schema run`, `schema progress`, `schema stop` or command help through the project's approved launcher before using a changed contract. A launcher can be an executable plus an argument array; do not flatten it into a shell string.

Configure `runtime.command` and `runtime.cwd` only with the user's intended official runtime or approved route wrapper. No implicit download, upgrade, login, account replacement or fallback runtime. `doctor` may install dependencies and provider `--live` checks call models; they are not harmless read-only status checks.

## Normal wrapper flow

1. For an exact website run, retain its entire executable, version and argument array. `prepare` registers the provided plan without starting tasks; it is stateful and requires the user's run/preparation scope.
2. Inspect `run --dry-run --json`, harness, model, effort, task count, concurrency, expiry and refill bounds. Do not broaden a fixed plan. New runs/refill exclude ultra and never silently downgrade it.
3. When the run is authorized, invoke wrapper `run --json`; the backend is the official CLI. If using `--command-file`, preserve `FilePath` / `ArgumentList` and enable exact dispatch only for the intended official command. Interpret structured errors and the official decision contract. Do not automatically answer all confirmations.
4. For **upload-only**, use wrapper `upload --json` for the same registered run. It delegates to official `run --upload-only --json`, preserving completed artifacts and avoiding rerunning questions. A historical ultra result can be uploaded if explicitly in scope; do not start another ultra task.
5. If there is no run-plan contract, inspect official `retry-upload --help` and the pending-upload ledger summary through approved tools. This command may cover multiple pending results; do not run it when that would exceed the user's selected submission scope. `resume` can start waiting tasks, so it is not an upload-only shortcut.
6. Check `progress` and fresh `submissions --records-scope all --period all --json`. Distinguish local completion, upload accepted, queued grading, graded, and points credited. Upload exit code 0 alone does not prove a passed grade. On ambiguous delivery, query progress before retrying.

## Managed routes

If AGENTS.md mandates a transport (such as Tenbin), use it for starts, resumes and refill. The generic wrapper deliberately refuses to replace a managed project's route. Keep the wrapper for queries, and delegate controls to the workspace's approved launcher. Exact website arrays go through that project's exact-command routing launcher. Never fall back to official model authentication because routing failed. Keep the single existing data directory and verify container route plus matching request evidence before claiming the model actually used it.

This exception is execution routing inside the CLI workflow, not a reason to replace the user's dashboard or to route read-only website queries through a model.

Stop on missing scope, invalid plan, unexpected official decision, routing mismatch or unrecoverable artifact error. Do not edit protected ledgers, reset authentication, salvage another owner's result, or release unrelated tasks to make an upload succeed.
