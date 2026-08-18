# Task 7 Implementation Report

## Status

Implemented first-run readiness diagnostics, bundled preset catalog metadata, controlled extension manifests and lifecycle hooks, and redacted MCP operation auditing. Existing workflow YAML files, workflow IDs, dispatch behavior, and plugins without manifests remain unchanged.

## Changed Files

- `kirara_ai/web/api/system/readiness.py`: added ordered, local, per-check-timeout readiness diagnostics with non-secret evidence.
- `kirara_ai/web/api/system/models.py`: added readiness response and check models.
- `kirara_ai/web/api/system/routes.py`: added authenticated `GET /api/system/readiness`.
- `kirara_ai/workflow/presets/catalog.py`: added catalog, Skill, Agent, and dispatch-preview validation models and helpers.
- `kirara_ai/workflow/presets/catalog.json`: added separate metadata for all 11 bundled YAML presets.
- `kirara_ai/web/api/workflow/routes.py`: added optional `metadata.catalog` to workflow list entries.
- `kirara_ai/plugin_manager/models.py`: added extension capability, lifecycle hook, and manifest contracts, including `config-write` input compatibility.
- `kirara_ai/plugin_manager/plugin_event_bus.py`: added capability checks, allowlisted lifecycle registration/emission, and structured audits while preserving legacy registration.
- `kirara_ai/plugin_manager/plugin_loader.py`: loaded optional manifests and injected manifest-scoped plugin event buses.
- `kirara_ai/mcp_module/manager.py`: added redacted audit events for tool, prompt, and resource operations.
- `tests/web/api/system/test_readiness.py`: covered authentication, stable ordering, bounded execution, and secret-free responses.
- `tests/test_preset_catalog.py`: covered bundled YAML completeness, metadata contracts, workflow validation, and dispatch previews.
- `tests/test_extension_manifest.py`: covered legacy behavior, lifecycle/capability rejection, manifest spelling, structured audits, and MCP redaction.

## Verification

Focused command required by the brief:

`.venv-win\Scripts\python.exe -m pytest tests/web/api/system/test_readiness.py tests/test_preset_catalog.py tests/test_extension_manifest.py tests/test_mcp_server.py -q`

Result:

`18 passed, 3 warnings in 6.53s`

The warnings are a Starlette/httpx deprecation warning and two test-only JWT HMAC key-length warnings. There were no test failures.

Graph update command:

`graphify update .`

Result: completed successfully and rebuilt `graphify-out` with 5,503 nodes, 12,766 edges, and 318 communities.

Additional checks:

- `git diff --check`: passed with no whitespace errors.
- `ExtensionCapabilities.model_validate({"config-write": True})`: accepted and authorized both manifest spelling and Python field spelling.

## Self-Review

- Readiness checks use stable IDs and deterministic ordering. Each check runs through `asyncio.to_thread` under `asyncio.wait_for`; failures expose only exception type and fixed remediation text.
- The readiness response never serializes configuration values. Evidence is limited to counts, booleans, timeout state, and exception type.
- Catalog validation uses the existing `WorkflowBuilder`, block validation, workflow registry, and `CombinedDispatchRule.explain_match`; it does not introduce an executor or alter YAML.
- Catalog API metadata is optional and additive under the existing workflow metadata field.
- Manifest-declared hooks are constrained to known lifecycle names and require an enabled declared capability. Unknown lifecycle/capability names are explicitly rejected.
- File, network, process, config-write, and secret checks reject access unless a manifest grants it, and the decision is audited without payload data.
- Plugins with no manifest retain the original general event registration and posting behavior.
- MCP audit records contain only component, server, operation, duration, outcome, and a fixed redacted error object. Tool arguments, prompt arguments, resource URIs, headers, environment values, and exception text are omitted.
- Existing MCP success/error return and propagation behavior is preserved.
- The protected handoff document, `docs/LOGO.jpg`, and `.venv` were not edited by this task and will not be staged.

## Concerns

- `graphify update .` reported an existing parse warning for `webui/src/components/ConfigurationList.vue` at line 150 and a zero-node warning for the data-only `catalog.json`; the update still completed successfully.
- The focused tests emit existing dependency/test-fixture warnings noted above; they do not affect the Task 7 contracts.

## Fix Round 1

### Changes

- `kirara_ai/plugin_manager/plugin_event_bus.py`, `kirara_ai/plugin_manager/plugin_loader.py`, and `kirara_ai/plugin_manager/extension_host.py`: enforce manifested capabilities at injected event/file/network/process/config-write/secret host operations, preserve parent-container dependency injection, bound audit history, and unregister lifecycle buses on stop/disable. Manifestless plugins and legacy `PluginLoaded`, `PluginStarted`, and `PluginStopped` production remain compatible.
- `kirara_ai/entry.py`, `kirara_ai/workflow/core/execution/executor.py`, `kirara_ai/mcp_module/manager.py`, `kirara_ai/web/api/dispatch/routes.py`, and `kirara_ai/scheduler/scheduler.py`: connect application, workflow, MCP, dispatch-preview, and model-catalog lifecycle producers to manifested hooks using sanitized summaries.
- `kirara_ai/web/api/system/readiness.py` and `kirara_ai/web/api/system/routes.py`: parse relevant user-owned config, workflow, and rule files without returning or logging their values; report malformed files through fixed sanitized failures; use four fixed workers plus nonblocking capacity admission.
- `kirara_ai/workflow/presets/catalog.py`, `kirara_ai/web/api/workflow/routes.py`, `pyproject.toml`, and `MANIFEST.in`: include `catalog.json` in installed artifacts, load it once per workflow-list request, and exclude local bytecode from wheel/sdist inputs.
- `kirara_ai/plugin_manager/models.py`: retain the explicit manifest capability and lifecycle contracts.
- `tests/test_extension_manifest.py`, `tests/workflow_executor/test_executor.py`, `tests/web/api/dispatch/test_dispatch.py`, and `tests/test_scheduler.py`: cover real loader dependency/capability behavior, legacy lifecycle events, and actual lifecycle producers.
- `tests/web/api/system/test_readiness.py`: cover malformed on-disk files, sanitized failures, real slow timeouts, and bounded admission.
- `tests/test_preset_catalog.py`, `tests/test_release_artifact_contract.py`, `tests/web/api/workflow/test_workflow.py`: cover package-data declaration, built archives, installed-path catalog loading, and workflow-list compatibility.

### Verification

- `rtk .venv-win\Scripts\python.exe -m pytest tests/test_extension_manifest.py tests/web/api/dispatch/test_dispatch.py tests/test_scheduler.py -q`
  - `27 passed, 21 warnings in 10.68s`
- `rtk .venv-win\Scripts\python.exe -m pytest tests/web/api/system/test_readiness.py tests/test_preset_catalog.py tests/test_extension_manifest.py tests/test_mcp_server.py -q`
  - `26 passed, 3 warnings in 8.11s`
- `rtk .venv-win\Scripts\python.exe -m pytest tests/web/api/system/test_readiness.py tests/test_preset_catalog.py tests/test_extension_manifest.py tests/test_mcp_server.py tests/web/api/workflow/test_workflow.py tests/workflow_executor/test_executor.py tests/test_release_artifact_contract.py tests/web/api/dispatch/test_dispatch.py tests/test_scheduler.py -q`
  - `59 passed, 1 skipped, 41 warnings in 13.95s`
- `rtk .venv-win\Scripts\python.exe -m build`
  - Exit 1: `No module named build`.
- `rtk .venv-win\Scripts\python.exe -m pip install build`
  - Exit 1: `No module named pip`; no other Python environment was used.
- `rtk .venv-win\Scripts\python.exe -c "from pathlib import Path; from setuptools.build_meta import build_sdist, build_wheel; Path('dist').mkdir(exist_ok=True); print(build_wheel('dist')); print(build_sdist('dist'))"`
  - Exit 0: generated `kirara_ai-3.3.0a7-py3-none-any.whl` and `kirara_ai-3.3.0a7.tar.gz` using setuptools 84.0.0 from `.venv-win`.
- `rtk pwsh -NoProfile -Command '$env:KIRARA_RELEASE_ARCHIVES = ((Resolve-Path "dist/kirara_ai-3.3.0a7-py3-none-any.whl").Path + [IO.Path]::PathSeparator + (Resolve-Path "dist/kirara_ai-3.3.0a7.tar.gz").Path); & ".venv-win\Scripts\python.exe" -m pytest tests/test_release_artifact_contract.py -q'`
  - `3 passed in 0.15s`; wheel and sdist both contain `kirara_ai/workflow/presets/catalog.json` and reject local/generated files.
- `rtk git diff --check`
  - Exit 0 with no output.
- `rtk graphify update .`
  - Exit 0: rebuilt 5,542 nodes, 12,973 edges, and 289 communities.

Warnings are limited to the existing Starlette/httpx deprecation warning, test-only JWT key-length warnings, the existing Graphify Vue parse warning at line 150, and the expected zero-node warning for data-only `catalog.json`.

### Concerns And Supported Boundary

- This is not a Python sandbox. A manifested plugin running in the Kirara AI process can import standard-library or third-party APIs directly. Capability enforcement covers only operations exposed through the injected Kirara AI host facade; process isolation would require a separate plugin runtime and IPC protocol.
- Python cannot forcibly terminate a worker thread that has already entered a blocking readiness probe. A timed-out probe occupies one of the four fixed workers until its function returns. The fixed executor and nonblocking semaphore prevent both worker count and accepted queued work from growing without bound.
