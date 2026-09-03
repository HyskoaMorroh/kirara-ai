"""Census: response keys the backend returns that no frontend type declares.

Motivation. Four defects in this repo shared one shape: the backend computes a
value, the API returns it, and the TypeScript interface does not declare the
field -- so the frontend reads `undefined` and the feature silently does
nothing. `tsc` cannot catch it, because passing an object that lacks an
optional field is legal.

  * cache_write tokens        -- returned, undeclared, never displayed
  * session channel identity  -- returned, undeclared, column blank
  * pricing display_name      -- returned, undeclared, label fell back to id
  * resource name/description -- projected, undeclared, two of three search
                                 faces never matched anything

This script is a *lead generator*, not a verdict. It cannot resolve values
through helper calls, so it over-reports (keys built by dict comprehensions,
keys only used server-side) and under-reports (responses assembled in helpers).
Every hit needs reading before it counts as a defect.

Method
------
Backend: collect string keys from dict literals inside functions decorated with
a route decorator, plus the `_KEY = value` shape used in projection helpers.
Frontend: collect property names from every `export interface` / `export type`
in `webui/src/api/*.ts`.

A key is reported when it appears in a backend response literal and in no
frontend interface at all.

Run: .venv/Scripts/python.exe scripts/audit_api_field_coverage.py
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

_ROUTE_DECORATOR = re.compile(r"\.route\(")

#: Keys that are transport or protocol level, never rendered as data.
_IGNORED = {
    "error",
    "message",
    "detail",
    "status",
    "ok",
    "success",
}


def _is_route_handler(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for decorator in node.decorator_list:
        if _ROUTE_DECORATOR.search(ast.unparse(decorator)):
            return True
    return False


def _dict_keys(node: ast.AST) -> set[str]:
    keys: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Dict):
            for key in child.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
    return keys


def backend_response_keys() -> dict[str, set[str]]:
    """Map "module::handler" -> response keys found in its dict literals."""

    found: dict[str, set[str]] = {}
    api_root = REPOSITORY_ROOT / "kirara_ai" / "web" / "api"
    for path in sorted(api_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        module = str(path.relative_to(REPOSITORY_ROOT)).replace("\\", "/")
        for node in ast.walk(tree):
            if not _is_route_handler(node):
                continue
            keys = _dict_keys(node) - _IGNORED
            if keys:
                found[f"{module}::{node.name}"] = keys
    return found


#: Service-layer helpers whose dict keys actually reach a client.
#:
#: Named explicitly rather than scanned wholesale: these modules also build
#: **persistence** formats (`_capture_state`, `_load_registry`, `_document`,
#: `_build_text_archive`) whose keys live on disk and are never serialized to
#: the browser. Reporting those is pure noise, and noise is what gets an audit
#: script ignored.
_PROJECTION_FUNCTIONS = {
    "kirara_ai/plugin_manager/resource_lifecycle.py": {
        "_snapshot",
        "get_storage_status",
        "read_entry_metadata",
        "list_backups",
    },
    "kirara_ai/plugin_manager/resource_catalog.py": {
        "project_dependencies",
        "_with_install_state",
        "_skill_record",
        "builtin_provisioning_report",
    },
    "kirara_ai/plugin_manager/system_dependencies.py": {"_public", "_task_public"},
    "kirara_ai/llm/resilience.py": {"snapshot"},
}


def projection_keys() -> dict[str, set[str]]:
    """Keys added by service-layer projection helpers.

    These never appear inside a route handler -- `_snapshot`,
    `project_dependencies` and friends live in the service layer, and their keys
    reach the client all the same. Missing them is exactly how the
    name/description defect stayed invisible: the route returned
    `_resource_response(...)`, and the two keys were added two layers down.
    """

    found: dict[str, set[str]] = {}
    for relative, wanted in _PROJECTION_FUNCTIONS.items():
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in wanted:
                continue
            keys = _dict_keys(node) - _IGNORED
            if keys:
                found[f"{relative}::{node.name}"] = keys
    return found


#: A declared property. **Not** anchored to line start: this codebase writes
#: whole interfaces on one line (`interface X { a: number; b: string }`), and a
#: `^\s*`-anchored pattern sees only the first property of each -- which made a
#: fully-declared `BackupInspection` look like five undeclared keys.
_TS_PROPERTY = re.compile(r"(?:^|[{;,])\s*(?:readonly\s+)?([A-Za-z_$][\w$]*)\s*\??\s*:", re.MULTILINE)

#: Inline response shapes: `http.get<{ supportsAutoDetectModels: boolean }>(...)`.
#:
#: This codebase declares plenty of one-off responses inline instead of as a
#: named interface. Reading only `export interface` reports every one of them as
#: undeclared -- which is how a first run of this script produced a page of
#: false positives.
_TS_INLINE_GENERIC = re.compile(r"<\{([^}]*)\}>", re.DOTALL)
_TS_INLINE_PROPERTY = re.compile(r"([A-Za-z_$][\w$]*)\s*\??\s*:")


def _fields_in(text: str) -> set[str]:
    fields = set(_TS_PROPERTY.findall(text))
    for block in _TS_INLINE_GENERIC.findall(text):
        fields.update(_TS_INLINE_PROPERTY.findall(block))
    # Destructuring a response also proves the field is known to the client:
    # `const { supportsAutoDetectModels } = await ...`.
    for block in re.findall(r"const\s*\{([^}]*)\}\s*=", text):
        fields.update(re.findall(r"([A-Za-z_$][\w$]*)", block))
    # Property reads: `row.cache_write_tokens`, `payload['rule_count']`.
    fields.update(re.findall(r"\.([a-z_][a-z0-9_]{2,})", text))
    fields.update(re.findall(r"\[['\"]([a-z_][a-z0-9_]*)['\"]\]", text))
    return fields


def frontend_declared_fields() -> set[str]:
    fields: set[str] = set()
    for extra in ("webui/src/api", "webui/src/views", "webui/src/stores", "webui/src/components"):
        root = REPOSITORY_ROOT / extra
        if not root.is_dir():
            continue
        for pattern in ("*.ts", "*.vue"):
            for path in root.rglob(pattern):
                fields.update(_fields_in(path.read_text(encoding="utf-8", errors="replace")))
    return fields


def main() -> None:
    declared = frontend_declared_fields()
    sources = {**backend_response_keys(), **projection_keys()}

    missing: dict[str, set[str]] = {}
    for origin, keys in sources.items():
        gap = {key for key in keys if key not in declared}
        if gap:
            missing[origin] = gap

    total = sum(len(keys) for keys in missing.values())
    print(f"frontend declares {len(declared)} distinct field names")
    print(f"backend sources scanned: {len(sources)}")
    print(f"undeclared keys: {total} across {len(missing)} sources\n")
    for origin in sorted(missing, key=lambda value: -len(missing[value])):
        keys = ", ".join(sorted(missing[origin]))
        print(f"{origin}\n    {keys}")


if __name__ == "__main__":
    main()
