# Kirara AI 3.3.0a7 Excellence Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make project B a release-proven, backward-compatible replacement for project A while improving workflow loading, persistence reliability, canvas interaction, visual quality, first-run usability, observability, and controlled extensibility.

**Architecture:** Preserve the existing Block, Workflow, DispatchRule, Plugin, EventBus, MCP, and configuration contracts. Add correctness at their existing boundaries: recoverable file transactions and locked snapshots in Python, request generations and compare-and-swap checks around asynchronous updates, spatial indexing and incremental layout in the canvas, and semantic design tokens above the current theme store. Ship only capabilities backed by tests, upgrade checks, clean artifacts, and user-facing operating documentation.

**Tech Stack:** Python 3.10-3.13, Quart, Pydantic, ruamel.yaml, uv, pytest, Vue 3, TypeScript, Vite, Vitest, Naive UI, Vue Flow, dagre, Docker Buildx, GitHub Actions.

## Global Constraints

- Release version is exactly `3.3.0a7` in Python metadata, lock data, release documentation, and build-time UI labels.
- Preserve project A and project B public APIs, configuration/YAML formats, workflow IDs, dispatch semantics, model dropdown selection, periodic model refresh, comments, function semantics, and user-owned data.
- Never overwrite an edited user workflow, resurrect an explicitly deleted bundled preset, or modify workflow `model_name` and fallback model slots during model-catalog refresh.
- Existing internal names remain valid; new helpers may be added only behind compatible interfaces.
- No destructive migration, silent fallback, unbounded retry, new mandatory remote service, or runtime network dependency.
- `docs/LOGO.jpg` is user-owned and must not be edited, moved, deleted, staged, or packaged.
- Every write involving multiple persisted files must be recoverable after interruption and leave either the old logical state or the new logical state after startup recovery.
- All UI additions must support keyboard access, visible focus, reduced motion, narrow desktop/mobile layouts, light/dark themes, and Chinese text without clipping.
- Do not add generic Agents, Skills, or Hooks labels unless they have a concrete manifest, permission boundary, lifecycle, audit record, and executable integration with existing primitives.

---

### Task 1: Version and Clean Release Contract

**Files:**
- Modify: `uv.lock`
- Modify: `webui/package.json`
- Modify: `webui/yarn.lock`
- Modify: `tests/test_release_workflow_contract.py`
- Create: `tests/test_release_artifact_contract.py`
- Modify: `.github/workflows/release-preflight.yml`

**Interfaces:**
- Consumes: `pyproject.toml` project version `3.3.0a7`, `VITE_APP_VERSION`, `MANIFEST.in`, setuptools package-data configuration.
- Produces: one source release version and `assert_distribution_contents(archive: Path) -> None` coverage for wheel/sdist contents.

- [x] **Step 1: Add failing metadata consistency tests**

  Extend `tests/test_release_artifact_contract.py` to parse `pyproject.toml`, `uv.lock`, and `webui/package.json`; assert the Python version is `3.3.0a7` and the npm-compatible WebUI package version is `3.3.0-a7`. Assert `kirara-ai` lock metadata equals `pyproject.toml` and `webui/yarn.lock` remains the declared Yarn lock.

- [x] **Step 2: Run the metadata tests and confirm stale lock/WebUI versions fail**

  Run: `uv run --isolated --frozen --python 3.11 python -m pytest tests/test_release_artifact_contract.py -q`

  Expected: failure naming `uv.lock` version `3.3.0a5` and WebUI version `0.1.1-beta.3` before implementation.

- [x] **Step 3: Regenerate authoritative lock and WebUI package metadata**

  Run `uv lock`, then set both WebUI package version fields to `3.3.0-a7` because npm semver does not accept PEP 440's compact prerelease form. Keep displayed release identity supplied by `VITE_APP_VERSION=v3.3.0a7`.

- [x] **Step 4: Add clean archive assertions**

  Implement `assert_distribution_contents` using `zipfile` and `tarfile`. Require backup modules, Alembic files, all bundled plugin assets, and all workflow presets. Reject `.pyc`, `__pycache__`, `.env`, `data/`, credentials, `docs/LOGO.jpg`, and repository-local virtual environments.

- [x] **Step 5: Add release-preflight build and archive inspection**

  Build with `uv build --out-dir dist-release-check` in a clean CI checkout and run the artifact contract against both generated archives. Keep release jobs read-only until existing publish steps.

- [x] **Step 6: Verify lock and package contracts**

  Run: `uv lock --check`

  Run: `uv run --isolated --frozen --python 3.11 python -m pytest tests/test_release_artifact_contract.py tests/test_release_workflow_contract.py -q`

  Expected: all tests pass and the lock reports no update required.

- [x] **Step 7: Commit the release contract**

  Commit only tracked release files with message `fix: unify 3.3.0a7 release metadata`.

### Task 2: Recoverable Workflow and Dispatch Persistence

**Files:**
- Create: `kirara_ai/workflow/persistence.py`
- Modify: `kirara_ai/workflow/core/workflow/registry.py`
- Modify: `kirara_ai/workflow/core/dispatch/registry.py`
- Modify: `kirara_ai/web/api/workflow/routes.py`
- Modify: `tests/test_workflow_preset_deletions.py`
- Modify: `tests/web/api/workflow/test_workflow.py`
- Modify: `tests/web/api/dispatch/test_dispatch.py`
- Create: `tests/test_workflow_persistence.py`

**Interfaces:**
- Produces: `FileMutation`, `FileTransaction.commit()`, `FileTransaction.recover_directory(path)`, `atomic_write_text(path, writer)`, `WorkflowRegistry.snapshot_builders()`, and `WorkflowRegistry.persist_builder(...)`.
- Consumes: existing YAML serializers, preset tombstone paths, registry locks, and route response schemas.

- [x] **Step 1: Add interruption and rollback tests**

  Cover failure after the first replacement in a two-file dispatch save, workflow create failure after YAML staging, rename failure before old-file removal, delete failure before tombstone publication, and startup recovery with a prepared transaction journal. Assert restart resolves each case to a complete old or complete new state.

- [x] **Step 2: Add the locked snapshot test**

  Start concurrent register/unregister operations while repeatedly calling `snapshot_builders()`. Assert snapshots are tuples of stable `(full_name, builder)` pairs and list routing never reads `_workflows` directly.

- [x] **Step 3: Run persistence tests and confirm current partial-commit cases fail**

  Run: `uv run --isolated --frozen --python 3.11 python -m pytest tests/test_workflow_persistence.py tests/test_workflow_preset_deletions.py tests/web/api/workflow/test_workflow.py tests/web/api/dispatch/test_dispatch.py -q`

- [x] **Step 4: Implement same-directory staged file transactions**

  `FileTransaction` writes a versioned JSON journal containing operation IDs, target paths, staged paths, backup paths, and phase. It fsyncs staged files and the journal, publishes replacements, fsyncs parent directories where supported, marks committed, removes backups, and finally removes the journal. Recovery completes a fully staged transaction or restores backups when publication was incomplete.

- [x] **Step 5: Route rule YAML and tombstones through one transaction**

  Snapshot rules and tombstones under `DispatchRuleRegistry._lock`, serialize outside shared mutation, and commit both files together. Preserve legacy rule loading, deletion markers, preset ownership, and `save_rules_async()` behavior.

- [x] **Step 6: Route workflow create, update, rename, and delete through registry persistence**

  Keep route URLs and response payloads unchanged. Perform filesystem publication and registry mutation as one coordinated operation with rollback. Rename publishes the new YAML before retiring the old YAML and updates the registry only after persistence succeeds.

- [x] **Step 7: Replace private registry iteration**

  Make `list_workflows()` consume `registry.snapshot_builders()`. Do not expose a mutable mapping or hold the registry lock while building HTTP payloads.

- [x] **Step 8: Recover journals before loading persisted state**

  Call `FileTransaction.recover_directory()` for workflow and dispatch directories before tombstones, YAML files, or bundled presets are loaded.

- [x] **Step 9: Verify persistence and legacy behavior**

  Run the Task 2 test command again. Expected: all interruption, preset deletion, route, and dispatch tests pass.

- [x] **Step 10: Commit the persistence boundary**

  Commit with message `fix: make workflow persistence recoverable`.

### Task 3: Model Catalog Compare-and-Swap

**Files:**
- Modify: `kirara_ai/scheduler/scheduler.py`
- Modify: `kirara_ai/scheduler/model_catalog.py`
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_model_catalog.py`

**Interfaces:**
- Produces: `backend_config_fingerprint(config) -> str` and a scheduler CAS check using backend name, adapter object identity, and pre-request configuration fingerprint.
- Consumes: `LLMManager.get`, `AutoDetectModelsProtocol.auto_detect_models`, `normalize_detected_models`, `CONFIG_UPDATE_LOCK`.

- [x] **Step 1: Add stale-adapter and edited-config tests**

  Delay `auto_detect_models()`, then replace the adapter or edit endpoint/auth/adapter configuration under the same backend name. Assert the old result is discarded, configuration is not saved, reload is not called, and scheduler state does not record a successful refresh.

- [x] **Step 2: Add preservation tests**

  Assert successful refresh changes only `backend.models`; it leaves backend name, adapter, endpoint, credentials, `model_name`, fallback slots, and user workflow drafts unchanged.

- [x] **Step 3: Run scheduler tests and confirm stale-result cases fail**

  Run: `uv run --isolated --frozen --python 3.11 python -m pytest tests/test_scheduler.py tests/test_model_catalog.py -q`

- [x] **Step 4: Implement stable configuration fingerprints and CAS**

  Hash a canonical JSON dump excluding only the model catalog. Capture adapter identity and fingerprint before awaiting detection. Under `CONFIG_UPDATE_LOCK`, re-resolve both and apply only when name, object identity, and fingerprint still match.

- [x] **Step 5: Verify scheduler behavior**

  Run the Task 3 test command. Expected: stale results are logged and discarded; valid updates preserve all manual selections.

- [x] **Step 6: Commit model refresh correctness**

  Commit with message `fix: reject stale model catalog refreshes`.

### Task 4: Frontend Request Generations

**Files:**
- Create: `webui/src/composables/useLatestRequest.ts`
- Create: `webui/tests/latest-request.test.ts`
- Modify: `webui/src/views/workflow/WorkflowEditor.vue`
- Modify: `webui/src/views/llm/LLMView.vue`
- Modify: `webui/tests/workflow-editor.test.ts`
- Create: `webui/tests/llm-view-requests.test.ts`

**Interfaces:**
- Produces: `useLatestRequest()` with `begin(): { generation, signal }`, `isCurrent(generation)`, and `cancel()`.
- Consumes: existing workflow and LLM API promises, route params, editor dirty state, pending edit flush, and Vue lifecycle hooks.

- [x] **Step 1: Test out-of-order workflow and schema responses**

  Resolve request A after request B. Assert only B updates visible data, A cannot clear loading for B, A cannot reset dirty state, and A can never become the target of a later save.

- [x] **Step 2: Test route leave and unmount cancellation**

  Assert outstanding controllers abort and resolved aborted promises do not mutate component state.

- [x] **Step 3: Run frontend request tests and observe current failures**

  Run: `npm --prefix webui test -- --run webui/tests/latest-request.test.ts webui/tests/workflow-editor.test.ts webui/tests/llm-view-requests.test.ts`

- [x] **Step 4: Implement the shared latest-request helper**

  Each `begin()` aborts the prior request, increments a monotonic generation, and returns a signal. `cancel()` aborts and increments generation so non-abortable promises also become stale.

- [x] **Step 5: Guard workflow load and save targets**

  Capture `groupId`, `workflowId`, generation, and loaded revision before awaiting. Apply data only when all still match. A save uses the editor's loaded identity rather than mutable route refs and rejects if the load generation changed.

- [x] **Step 6: Guard LLM schema and support requests**

  Reuse the same generation rule for schema and capability lookups. Keep manually entered adapter configuration unless the current adapter type's current request explicitly replaces it.

- [x] **Step 7: Verify frontend request ordering**

  Run the Task 4 test command. Expected: all ordering, cancellation, dirty-state, and save-target tests pass.

- [x] **Step 8: Commit request-generation guards**

  Commit with message `fix: ignore stale editor requests`.

### Task 5: Incremental Canvas Layout and Bounded History

**Files:**
- Modify: `webui/src/components/workflow/useLayout.ts`
- Create: `webui/src/components/workflow/spatial-index.ts`
- Modify: `webui/src/components/workflow/WorkflowCanvas.vue`
- Modify: `webui/src/store/workflow-editor.ts`
- Modify: `webui/src/utils/deep-clone.ts`
- Modify: `webui/tests/workflow-layout.test.ts`
- Modify: `webui/tests/workflow-editor.test.ts`
- Create: `webui/tests/workflow-spatial-index.test.ts`

**Interfaces:**
- Produces: `GridSpatialIndex`, `layoutMissingNodes(...)`, immutable `WorkflowGraphSnapshot`, and bounded history with unchanged `undo()`, `redo()`, and `performActionWithoutHistory()` entry points.
- Consumes: Vue Flow node dimensions, dagre layout, existing 20 px grid, validation issue model, and pending edit flush.

- [x] **Step 1: Add correctness and scale tests**

  Generate 1,000 deterministic nodes and assert overlap queries return the same pairs as a brute-force oracle. Assert opening a graph with valid saved positions never runs full dagre layout. Assert only missing/invalid positions receive new coordinates and all produced boxes are non-overlapping.

- [x] **Step 2: Add bounded undo/redo tests**

  Assert 101 graph changes retain the configured latest 100 entries, redo invalidates after a new edit, no-op edits do not create history, and nested config data remains isolated across snapshots.

- [x] **Step 3: Run layout and history tests**

  Run: `npm --prefix webui test -- --run webui/tests/workflow-layout.test.ts webui/tests/workflow-spatial-index.test.ts webui/tests/workflow-editor.test.ts`

- [x] **Step 4: Implement grid-bucket spatial indexing**

  Index each node box into covered fixed-size cells, deduplicate candidate IDs, and perform exact rectangle checks only for candidates. Use it in `findFreeNodePosition`, `findOverlappingNodes`, and post-layout collision resolution.

- [x] **Step 5: Preserve valid user coordinates and lay out only missing nodes**

  Treat finite coordinates as user-owned. Anchor missing nodes near connected positioned neighbors, then place disconnected nodes in deterministic lanes. Full auto-layout remains an explicit undoable command.

- [x] **Step 6: Replace repeated whole-graph deep clones with immutable snapshots**

  Reuse unchanged block/config/wire references, clone only mutated records, cap history at 100, and retain serializable isolation. Keep existing editor method names and pending edit flush semantics.

- [x] **Step 7: Verify scale, determinism, and history**

  Run the Task 5 test command. Expected: 1,000-node tests complete without overlaps and all existing undo/redo tests pass.

- [x] **Step 8: Commit canvas performance work**

  Commit with message `perf: make workflow canvas scale predictably`.

### Task 6: Semantic Theme and Accessible Canvas Controls

**Files:**
- Modify: `webui/src/theme/palettes.ts`
- Modify: `webui/src/stores/theme.ts`
- Modify: `webui/src/App.vue`
- Modify: `webui/src/components/workflow/WorkflowCanvas.vue`
- Modify: `webui/src/components/workflow/NodeConfigPanel.vue`
- Modify: `webui/src/components/workflow/WorkflowNode.vue`
- Modify: `webui/src/views/workflow/WorkflowEditor.vue`
- Modify: `webui/tests/theme-boot-table.test.ts`
- Create: `webui/tests/workflow-accessibility.test.ts`

**Interfaces:**
- Produces: semantic tokens for canvas, panel, border, focus, success, warning, error, muted text, overlay, selection, and node type accents.
- Consumes: current palette IDs and persisted theme preference; existing palette IDs remain valid.

- [x] **Step 1: Add token contrast and keyboard tests**

  Assert every palette defines all semantic tokens, text/background combinations meet WCAG AA for normal text, icon buttons expose accessible names/tooltips, focus is visible, and canvas actions are reachable by keyboard.

- [x] **Step 2: Add responsive layout tests**

  Mount at 360 px, 768 px, and 1440 px widths. Assert toolbar commands do not overlap, labels wrap or collapse into icon tooltips, and the config panel remains reachable without covering the full canvas unexpectedly.

- [x] **Step 3: Run visual-contract tests**

  Run: `npm --prefix webui test -- --run webui/tests/theme-boot-table.test.ts webui/tests/workflow-accessibility.test.ts`

- [x] **Step 4: Introduce semantic tokens without changing palette identity**

  Map existing palette colors into neutral surfaces plus restrained functional accents. Remove one-hue dominance, preserve dark/light behavior, and set `color-scheme`, focus rings, selection, grid dots, minimap, and overlays from tokens.

- [x] **Step 5: Refine the canvas toolbar and inspector**

  Use the installed icon set for undo, redo, zoom, fit, layout, validation, minimap, and save. Keep text only for primary commands where ambiguity remains. Add tooltips, stable 36 px controls, segmented layout direction, and a responsive overflow menu.

- [x] **Step 6: Prevent text and panel collisions**

  Apply min/max panel tracks, `min-width: 0`, overflow wrapping for IDs/model names, stable node widths, line clamps only where full text remains available by tooltip, and reduced-motion transitions.

- [x] **Step 7: Verify tests and browser screenshots**

  Run the Task 6 tests, then capture light/dark screenshots at 360x800, 768x1024, and 1440x900. Assert no clipping, toolbar collision, nested cards, blank canvas, or low-contrast controls.

- [x] **Step 8: Commit visual refinements**

  Commit with message `feat: refine workflow canvas experience`.

### Task 7: First-Run Readiness, Preset Discoverability, and Controlled Extensions

**Files:**
- Create: `kirara_ai/web/api/system/readiness.py`
- Modify: `kirara_ai/web/api/system/models.py`
- Modify: `kirara_ai/web/api/system/routes.py`
- Create: `kirara_ai/workflow/presets/catalog.py`
- Create: `kirara_ai/workflow/presets/catalog.json`
- Modify: `kirara_ai/web/api/workflow/routes.py`
- Modify: `kirara_ai/plugin_manager/models.py`
- Modify: `kirara_ai/plugin_manager/plugin_loader.py`
- Modify: `kirara_ai/plugin_manager/plugin_event_bus.py`
- Modify: `kirara_ai/mcp_module/manager.py`
- Create: `tests/web/api/system/test_readiness.py`
- Create: `tests/test_preset_catalog.py`
- Create: `tests/test_extension_manifest.py`

**Interfaces:**
- Produces: `GET /api/system/readiness`, additive workflow catalog metadata, `ExtensionCapabilities`, permission-checked lifecycle hook registration, and MCP audit events.
- Consumes: existing workflow validation, dispatch preview/reachability, PluginLoader, PluginEventBus, MCPServerManager, and bundled presets.

- [ ] **Step 1: Test readiness diagnostics**

  Assert stable check IDs for writable data directories, configuration parseability, workflow validity, dispatch target existence, configured IM/LLM availability, and optional MCP health. Secret values must never appear in responses or logs.

- [ ] **Step 2: Test preset catalog completeness**

  Require every bundled YAML to have a stable ID, Chinese name, concise purpose, prerequisites, trigger examples, capabilities, and difficulty. Validate all referenced workflows and example dispatch previews.

- [ ] **Step 3: Test extension manifests and permissions**

  A plugin with no manifest retains current behavior. A declared hook requires a known lifecycle name and capability. Undeclared file, network, process, config-write, or secret access is rejected and audited. MCP tool/resource/prompt calls include server, operation, duration, outcome, and redacted error metadata.

- [ ] **Step 4: Implement additive readiness API**

  Return `ready`, timestamp, and ordered checks with `id`, `status`, `summary`, `remediation`, and non-secret evidence. Checks are local and bounded by per-check timeouts.

- [ ] **Step 5: Implement preset catalog without changing YAML semantics**

  Catalog metadata is separate from workflow YAML. Existing IDs and trigger behavior remain unchanged. API responses add optional metadata fields so older clients continue working.

- [ ] **Step 6: Add concrete extension capability manifests**

  Define lifecycle names around current events: startup completed, shutdown requested, workflow before/after/error, dispatch preview, model catalog refreshed, and MCP operation. Implement registration through PluginEventBus with allowlisted capabilities and structured audit records.

- [ ] **Step 7: Model Agents and Skills as compositions, not new runtimes**

  Document and validate an Agent as a workflow plus model/tool/memory policy metadata; model a Skill as a versioned workflow template with inputs, outputs, prerequisites, and examples. Do not introduce a second executor or bypass Block validation.

- [ ] **Step 8: Verify first-run and extension tests**

  Run: `uv run --isolated --frozen --python 3.11 python -m pytest tests/web/api/system/test_readiness.py tests/test_preset_catalog.py tests/test_extension_manifest.py tests/test_mcp_server.py -q`

- [ ] **Step 9: Commit readiness and extension controls**

  Commit with message `feat: add readiness and extension contracts`.

### Task 8: Documentation, Changelog, Docker, and Migration

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/QUICKSTART.md`
- Modify: `docs/EXCELLENCE_DEPLOYMENT_GUIDE.md`
- Modify: `docs/OBSERVABILITY.md`
- Modify: `docs/EXTENDING.md`
- Modify: `docs/WORKFLOW_OPERATIONS_GUIDE.md`
- Create: `docs/UPGRADING_TO_3.3.0a7.md`
- Create: `docs/AGENTS_SKILLS_HOOKS_MCP_GUIDE.md`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `.github/workflows/run-tests.yml`
- Modify: `.github/workflows/quickstart-windows.yml`
- Modify: `.github/workflows/release-preflight.yml`

**Interfaces:**
- Produces: copy-paste deployment and upgrade procedures, readiness troubleshooting, backup/rollback steps, extension tutorial, and CI release gates.
- Consumes: implemented endpoints, catalog schema, transaction recovery, build arguments, and existing backup APIs.

- [ ] **Step 1: Document exact upgrade and rollback sequence**

  Include pre-upgrade backup, A data-directory copy, `3.3.0a7` install, readiness checks, workflow/dispatch verification, model dropdown/manual selection check, periodic refresh observation, rollback, and backup restoration.

- [ ] **Step 2: Document built-in workflow and trigger recipes**

  Cover `/help`, memory clearing, dice, gacha, group `/chat`, mention/private/fallback memory, multimodal, long reply splitting, time-aware chat, function calling, custom script, sensitive-word filter, and MCP tools. Each recipe names prerequisites, sample trigger, expected workflow, and diagnostic endpoint.

- [ ] **Step 3: Write the practical Agents/Skills/Hooks/MCP guide**

  Show one workflow-backed Agent, one catalog-backed Skill, one permissioned lifecycle hook, and one MCP tool integration. Include manifest fields, least-privilege choices, audit output, failure behavior, and removal steps.

- [ ] **Step 4: Synchronize changelog and deployment surfaces**

  Record fixed races, transaction recovery, preserved behavior, canvas improvements, readiness, extension controls, migration notes, and known limits. Inject `VITE_APP_VERSION` in all Docker and Windows build paths.

- [ ] **Step 5: Add CI gates**

  Run backend suites on supported Python versions, frontend type/test/build, clean package build, archive inspection, Docker build, readiness smoke test, and A-data upgrade fixture. No publish job runs when a gate fails.

- [ ] **Step 6: Validate documentation references**

  Run repository link/reference checks and `rg` for stale `3.3.0a5`, contradictory endpoint names, placeholder markers, and commands that no longer exist. Expected: only historical comparison references remain.

- [ ] **Step 7: Commit delivery documentation**

  Commit with message `docs: complete 3.3.0a7 operations guide`.

### Task 9: Full Replacement Proof

**Files:**
- Create: `tests/fixtures/a4-data/README.md`
- Create: `tests/test_a4_upgrade_contract.py`
- Modify: `docs/superpowers/specs/2026-08-17-kirara-ai-excellence-overhaul-design.md`
- Modify: `docs/superpowers/plans/2026-08-17-kirara-ai-excellence-overhaul.md`

**Interfaces:**
- Produces: reproducible evidence that B `3.3.0a7` loads an anonymized A-format data fixture and preserves required behavior.
- Consumes: all previous tasks.

- [ ] **Step 1: Build a minimal anonymized A-format fixture**

  Include legacy rules, edited preset, deleted-preset tombstone, manual model choices, fallback slots, custom workflow positions, and plugin/MCP configuration with inert local endpoints. No user secrets or personal data.

- [ ] **Step 2: Test A-to-B upgrade behavior**

  Copy the fixture to a temporary data directory, start registries, run recovery, and assert all required built-ins and custom records load; edited presets stay edited; deleted presets stay deleted; manual model slots remain unchanged; rules dispatch to the same workflow IDs.

- [x] **Step 3: Run full backend verification**

  Run: `uv run --isolated --frozen --python 3.11 python -m pytest ./tests -q`

  Expected: all backend tests pass with no new unhandled warnings.

- [x] **Step 4: Run full frontend verification**

  Run: `npm --prefix webui run type-check`

  Run: `npm --prefix webui test -- --run`

  Run: `npm --prefix webui run build`

  Expected: TypeScript, all Vitest tests, and production build pass.

- [ ] **Step 5: Build and inspect clean distributions**

  Run: `uv build --out-dir dist-release-check`

  Run the artifact contract against wheel and sdist. Install the wheel into a fresh isolated environment and run import, CLI help, workflow preset, plugin asset, Alembic, backup, and readiness smoke checks.

- [ ] **Step 6: Build and smoke-test Docker**

  Build with `--build-arg VITE_APP_VERSION=v3.3.0a7`, start with a temporary volume, wait for health/readiness, inspect the UI version, and stop without publishing or deleting user volumes.

- [ ] **Step 7: Refresh the project graph**

  Run: `graphify update .`

  Expected: incremental AST update completes and graph queries resolve all new transaction, readiness, catalog, and extension symbols.

- [ ] **Step 8: Run five-axis review and diff hygiene**

  Review correctness, readability, architecture, security, and performance. Run `git diff --check`, confirm `docs/LOGO.jpg` remains untracked and unchanged, and confirm no cache/build artifact is staged.

- [ ] **Step 9: Record evidence and replacement decision**

  Mark completed plan steps, add exact test totals and artifact names, and state whether project B meets the replacement gate. A positive decision requires every mandatory Task 9 check to pass; unavailable Docker execution must be reported as an unresolved release risk rather than silently accepted.

- [ ] **Step 10: Commit final proof**

  Commit tracked tests and plan evidence with message `test: prove project a upgrade compatibility`.
