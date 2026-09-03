"""Census: backend routes that no frontend code ever calls.

Motivation. This repo has repeatedly shipped endpoints with no way to reach
them from the product:

  * `/llm/auto-detect-schedule` (x3) -- documented as "no WebUI, API only", so
    "the model catalog refreshes periodically" was invisible: nobody could
    answer when the next run was, whether the last one succeeded, or change an
    interval without editing `config.yaml` and restarting.
  * `/tracing/llm/export` -- existed for months with no button anywhere.
  * `/resources/imports` -- listing staged archives had no UI, so an operator
    who scp'd packages onto the server had no way to install them.

The shape is not "a missing feature". The capability is built, tested and
documented; it just cannot be used. That reads as absence to every user.

This script is a *lead generator*, not a verdict. Some routes legitimately have
no browser caller:

  * machine-facing endpoints (webhooks, health probes, OpenAI-compatible API)
  * endpoints called with a computed path the regex cannot resolve
  * download links opened as plain URLs rather than fetched

Every hit needs reading before it counts as a gap.

Run: .venv/Scripts/python.exe scripts/audit_unreachable_routes.py
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: Blueprint variable -> URL prefix, read from `register_blueprint` calls so the
#: table cannot drift from the app wiring.
_PREFIX_RE = re.compile(
    r"register_blueprint\(\s*([A-Za-z_][\w.]*)\s*,\s*url_prefix=[\"']([^\"']+)[\"']"
)
_ROUTE_RE = re.compile(r"@([A-Za-z_][\w]*)\.route\(\s*[\"']([^\"']*)[\"']")


def blueprint_prefixes() -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for path in (REPOSITORY_ROOT / "kirara_ai" / "web").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for variable, prefix in _PREFIX_RE.findall(text):
            prefixes[variable.rsplit(".", 1)[-1]] = prefix
    return prefixes


def backend_routes() -> list[tuple[str, str, str]]:
    """(path, source file, handler) for every registered route."""

    prefixes = blueprint_prefixes()
    found: list[tuple[str, str, str]] = []
    for path in sorted((REPOSITORY_ROOT / "kirara_ai" / "web" / "api").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        module = str(path.relative_to(REPOSITORY_ROOT)).replace("\\", "/")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        handlers: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    handlers[getattr(decorator, "lineno", -1)] = node.name
        for number, line in enumerate(text.splitlines(), 1):
            match = _ROUTE_RE.search(line)
            if not match:
                continue
            variable, rule = match.groups()
            prefix = prefixes.get(variable)
            if prefix is None:
                continue
            full = (prefix.rstrip("/") + "/" + rule.lstrip("/")).rstrip("/") or "/"
            found.append((full, module, handlers.get(number, "?")))
    return found


#: Frontend request paths. Covers `http.get('/x')`, template literals
#: (`/resources/${encodeURIComponent(id)}/enable`) and `http.fetch('/x', ...)`.
#:
#: The interpolation body must allow parentheses and dots: this codebase writes
#: `${encodeURIComponent(resourceId)}` almost everywhere, and a `${[^}]*}`
#: pattern stops at the first `}` -- which is *inside* the call, leaving the
#: literal unmatched. A first run of this script reported all 28 resource
#: routes as unreachable for exactly that reason.
#: `?`, `=` and `&` are in the class because query strings appear inside the
#: literal (`/resources/audit?${params.toString()}`); they are stripped during
#: normalization, but a literal that does not *match* is never seen at all.
_CALL_RE = re.compile(r"[\"'`](/[A-Za-z0-9_\-/${}().:,?=&\s]*)[\"'`]")


def frontend_paths() -> set[str]:
    paths: set[str] = set()
    root = REPOSITORY_ROOT / "webui" / "src"
    for pattern in ("*.ts", "*.vue", "*.js"):
        for path in root.rglob(pattern):
            text = path.read_text(encoding="utf-8", errors="replace")
            paths.update(_CALL_RE.findall(text))
    return paths


def _normalize(path: str) -> str:
    """Collapse every parameter to `*` so `<id>` and `${id}` compare equal.

    The `${...}` pattern is greedy up to the **last** `}` on the segment
    because the interpolation usually contains a call:
    `${encodeURIComponent(resourceId)}`. A lazy match would leave `)}` behind
    and the two sides would never line up.
    """

    # Query strings and fragments are not part of the route. The frontend writes
    # `/resources/audit?${params.toString()}` -- keeping the `?...` tail made six
    # genuinely-called routes look unreachable.
    path = path.split("?", 1)[0].split("#", 1)[0]
    path = re.sub(r"<[^>]+>", "*", path)
    path = re.sub(r"\$\{.*?\}\)?", "*", path)
    path = re.sub(r":[A-Za-z_][\w]*", "*", path)
    # `/api/resources/*/enable` and `/api/resources/*/enable` must compare equal
    # even when one side wrote two adjacent parameters.
    path = re.sub(r"\*+", "*", path)
    return path.rstrip("/") or "/"


def main() -> None:
    #: The frontend's `http` helper prepends `/backend-api/api`, so its literals
    #: start at the blueprint prefix minus `/api`.
    literals = frontend_paths()
    called: set[str] = set()
    for item in literals:
        for candidate in (_normalize("/api" + item), _normalize(item)):
            called.add(candidate)
            # `/resources/backups${query}` normalizes to `/resources/backups*`,
            # where the interpolation is an **optional query string** rather than
            # a path segment. Register the bare form too, otherwise six
            # genuinely-called routes read as unreachable.
            if candidate.endswith("*"):
                called.add(candidate[:-1].rstrip("/") or "/")
            # A leading `*` comes from a prefix helper:
            # `` `${getApiPrefix()}/traces` `` where the helper returns
            # `/tracing/llm`. Register the tail so `/api/tracing/llm/traces`
            # counts as called; this is exactly how the trace list, statistics
            # and detail endpoints are reached.
            if candidate.startswith("*") and candidate != "*":
                called.add("/api" + candidate[1:])
                called.add(candidate[1:])

    routes = backend_routes()
    unreachable = [
        (path, module, handler)
        for path, module, handler in routes
        if _normalize(path) not in called
    ]

    print(f"backend routes: {len(routes)}")
    print(f"frontend path literals: {len(called)}")
    print(f"no frontend caller found: {len(unreachable)}\n")
    print(
        "Read every hit before calling it a gap. Known legitimate reasons:\n"
        "  * a granular route superseded by a bulk one the UI actually calls\n"
        "    (`/agents/<id>/channels` vs `PUT /agents/<id>/configuration`)\n"
        "  * machine-facing endpoints and health probes\n"
        "  * URLs opened as links rather than fetched (`/media/file/<id>`)\n"
    )
    by_module: dict[str, list[str]] = {}
    for path, module, handler in unreachable:
        by_module.setdefault(module, []).append(f"{path}  ({handler})")
    for module in sorted(by_module, key=lambda value: -len(by_module[value])):
        print(module)
        for item in sorted(by_module[module]):
            print(f"    {item}")


if __name__ == "__main__":
    main()
