# A4 Upgrade Fixture

This fixture is an anonymized, offline data directory representing the data
shape consumed by the previous A-format release. It contains no credentials,
personal identifiers, or reachable services.

The fixture covers:

- a legacy dispatch-rule list using the pre-`rule_groups` fields;
- an edited copy of the bundled `chat:plain_text` preset;
- a workflow preset tombstone for `chat:time_aware`;
- manual model choices in the legacy string-list form, plus workflow fallback slots;
- a custom workflow with persisted canvas positions;
- plugin configuration and an explicitly disabled MCP server;
- inert local endpoints at port 9, which are never contacted by the contract test.

`tests/test_a4_upgrade_contract.py` copies this directory into a temporary data
directory, runs transaction recovery, and loads the real workflow and dispatch
registries. The fixture is deliberately small so it can remain a deterministic
release gate rather than a production backup.

Field provenance used by the compatibility proof:

- the legacy dispatch fields (`type`, rule-specific values, `rule_id`, and
  `workflow_id`) are converted by
  `kirara_ai/workflow/core/dispatch/registry.py::_convert_old_rule`;
- workflow YAML, node positions, and block kwargs are read by
  `kirara_ai/workflow/core/workflow/builder.py::WorkflowBuilder.load_from_yaml`;
- preset deletion state is loaded from `workflows/.preset_tombstones.json` by
  `WorkflowRegistry._load_preset_tombstones` before bundled presets are
  extracted;
- the legacy model string list is parsed by the existing `GlobalConfig` model
  loader. The test checks the resulting `model.id` values and does not invoke
  model discovery.

The test then writes the loaded workflow and dispatch registry back to the
temporary directory and reloads both. This proves persistence of the values
above, not just one in-memory parse.
