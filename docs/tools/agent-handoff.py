#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent-handoff — 把一个代码仓库的当前状态固化成"可无损接续"的交接现场。

用途：Codex / Claude Code 会话因上游 400、供应商熔断、上下文超限等原因卡死时，
在新会话开始前运行本脚本，一次完成三步：
  1. 提交快照（自动排除计划文档声明为"用户私有"的文件）
  2. 按客观证据回填计划文档的复选框
  3. 生成交接 Markdown + 新会话开场提示词

设计原则：不硬编码任何项目名、路径、任务名或测试命令。
项目相关信息全部从仓库自身推断：
  git 元数据              -> 分支 / HEAD / 未提交改动
  pyproject / package.json -> 技术栈与测试命令
  计划文档 Files: 段        -> 每个任务应产出哪些文件
  计划文档 Interfaces: 段   -> 每个任务应产出哪些符号
  计划文档 约束段           -> 哪些文件不得提交
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHECKBOX = re.compile(r"^(?P<indent>\s*)- \[(?P<mark>[ xX])\] (?P<body>.*)$", re.M)
TASK_HEAD = re.compile(r"^#{2,4}\s+(?P<title>(?:Task|任务)\s*(?P<num>\d+)[^\n]*)$", re.M)
FILE_LINE = re.compile(r"^\s*-\s*(?P<verb>Create|Modify|新建|修改)\s*:\s*`?(?P<path>[^`\s]+)`?", re.I)
BACKTICK = re.compile(r"`([^`]+)`")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PROTECTED_HINT = re.compile(
    r"`(?P<path>[^`\n]{1,120})`[^\n]{0,160}?"
    r"(?:user-owned|用户私有|must not be[^\n]{0,80}?(?:staged|packaged|committed)|不得[^\n]{0,40}?(?:提交|打包))",
    re.I,
)
PY_PASS = re.compile(r"(\d+) passed")
PY_FAIL = re.compile(r"(\d+) failed")
PY_FAILED_ID = re.compile(r"^FAILED (\S+)", re.M)
VITEST_TESTS = re.compile(r"Tests\s+(?P<n>\d+)\s+passed", re.I)

# --- session vitals -------------------------------------------------------
# Signatures that killed a real session rather than merely annoying it.
FATAL_SIG = re.compile(r"content-blocked|熔断|无可用渠道|IMAGE_DIMENSION_EXCEEDED")
# Measured on 54 Claude + 54 Codex transcripts: no transcript under 250 KB carried
# a fatal signature; every one over 8 MB did. These are the observed break points.
VITALS_BANDS = [
    (8_000_000, "critical", "立刻交接。同尺寸区间的会话 100% 出现过致命错误。"),
    (3_000_000, "high", "尽快交接。该区间约三成会话出现过致命错误。"),
    (1_000_000, "watch", "可以继续，但留意；该区间约一成七出现过致命错误。"),
    (0, "ok", "尺寸健康。"),
]


def agent_session_dirs() -> list[tuple[str, Path]]:
    """Where each installed agent keeps its transcripts. Absent dirs are skipped."""
    home = Path(os.path.expanduser("~"))
    candidates = [
        ("Claude Code", home / ".claude" / "projects"),
        ("Codex", home / ".codex" / "sessions"),
    ]
    return [(n, p) for n, p in candidates if p.is_dir()]


def scan_session_vitals(limit: int = 12) -> list[dict]:
    """Rank the newest transcripts by risk. Streams each file; keeps only counters."""
    rows: list[dict] = []
    for agent, root in agent_session_dirs():
        files = [p for p in root.rglob("*.jsonl") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for fp in files[:limit]:
            size = fp.stat().st_size
            fatal = 0
            errors = 0
            try:
                with fp.open(encoding="utf-8", errors="replace") as fh:
                    for raw in fh:
                        if FATAL_SIG.search(raw):
                            fatal += 1
                        if '"is_error":true' in raw or '"isError":true' in raw:
                            errors += 1
            except OSError:
                continue
            band, advice = "ok", VITALS_BANDS[-1][2]
            for threshold, name, text in VITALS_BANDS:
                if size >= threshold:
                    band, advice = name, text
                    break
            rows.append(
                {
                    "agent": agent,
                    "file": fp.name,
                    "mtime": datetime.fromtimestamp(fp.stat().st_mtime),
                    "mb": size / 1e6,
                    "fatal": fatal,
                    "errors": errors,
                    "band": band,
                    "advice": advice,
                }
            )
    order = {"critical": 0, "high": 1, "watch": 2, "ok": 3}
    rows.sort(key=lambda r: (order[r["band"]], -r["mb"]))
    return rows



def run(cmd: list[str] | str, cwd: Path, timeout: int = 60) -> tuple[int, str]:
    """Run a command, never raise. Returns (returncode, combined output)."""
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            shell=isinstance(cmd, str),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"<timeout after {timeout}s>"
    except FileNotFoundError:
        return 127, "<command not found>"
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return 1, f"<{type(exc).__name__}: {exc}>"


def git(repo: Path, *args: str, timeout: int = 60) -> str:
    code, out = run(["git", *args], repo, timeout)
    return out.strip() if code == 0 else ""


@dataclass
class Step:
    task_num: int
    number: int
    line_index: int
    text: str
    done: bool
    evidence: str = ""


@dataclass
class Task:
    num: int
    title: str
    files: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)


def find_plan(repo: Path, explicit: str | None) -> Path | None:
    """Locate the implementation plan. Newest checkbox-bearing markdown wins."""
    if explicit:
        p = (repo / explicit) if not os.path.isabs(explicit) else Path(explicit)
        return p if p.is_file() else None

    candidates: list[tuple[float, Path]] = []
    skip_dirs = {".git", "node_modules", ".venv", ".venv-win", "dist", "build", "__pycache__"}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        depth = len(Path(root).relative_to(repo).parts)
        if depth > 5:
            dirs[:] = []
            continue
        for fn in files:
            if not fn.lower().endswith(".md"):
                continue
            fp = Path(root) / fn
            try:
                head = fp.read_text(encoding="utf-8", errors="replace")[:60000]
            except OSError:
                continue
            # A plan has task headers AND checkboxes; a handoff/readme has neither.
            if len(CHECKBOX.findall(head)) < 3:
                continue
            if not TASK_HEAD.search(head):
                continue
            candidates.append((fp.stat().st_mtime, fp))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def parse_plan(text: str) -> tuple[list[Task], list[str]]:
    """Extract tasks (files, symbols, steps) and protected paths from the plan."""
    lines = text.splitlines()
    tasks: list[Task] = []
    current: Task | None = None
    section: str | None = None

    for idx, raw in enumerate(lines):
        head = TASK_HEAD.match(raw)
        if head:
            current = Task(num=int(head.group("num")), title=head.group("title").strip())
            tasks.append(current)
            section = None
            continue

        low = raw.strip().lower()
        if low.startswith("**files") or low.startswith("**文件"):
            section = "files"
            continue
        if low.startswith("**interfaces") or low.startswith("**接口"):
            section = "interfaces"
            continue
        if raw.startswith("**") and section:
            section = None

        if current is None:
            continue

        if section == "files":
            fm = FILE_LINE.match(raw)
            if fm:
                current.files.append(fm.group("path").strip())
            continue

        if section == "interfaces" and ("Produces" in raw or "产出" in raw):
            for token in BACKTICK.findall(raw):
                base = token.split("(")[0].split("[")[0].strip()
                base = base.split(".")[-1] if "." in base and " " not in base else base
                m = IDENT.fullmatch(base)
                if m and len(base) > 3:
                    current.symbols.append(base)
            continue

        cb = CHECKBOX.match(raw)
        if cb:
            body = cb.group("body")
            sm = re.match(r"\*\*(?:Step|步骤)\s*(\d+)", body)
            if sm:
                current.steps.append(
                    Step(
                        task_num=current.num,
                        number=int(sm.group(1)),
                        line_index=idx,
                        text=re.sub(r"\*\*", "", body)[:160],
                        done=cb.group("mark").lower() == "x",
                    )
                )

    protected = set()
    for m in PROTECTED_HINT.finditer(text):
        cand = m.group("path").strip()
        # A protected path must look like a path, not a sentence fragment.
        if not cand or " " in cand or "\n" in cand or len(cand) > 120:
            continue
        if "/" not in cand and "." not in cand:
            continue
        protected.add(cand)
    return tasks, sorted(protected)


def grep_symbol(repo: Path, symbol: str, hint_files: list[str]) -> bool:
    """Is the symbol actually defined somewhere? Prefer the task's own files."""
    pattern = rf"\b(def|class|function|const|let|var|export)\s+{re.escape(symbol)}\b"
    for rel in hint_files:
        fp = repo / rel
        if not fp.is_file():
            continue
        try:
            if re.search(pattern, fp.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    code, out = run(["rg", "-l", "--no-messages", pattern], repo, timeout=30)
    if code == 0 and out.strip():
        return True
    if code == 127:  # no ripgrep: fall back to git grep
        return bool(git(repo, "grep", "-lE", pattern, timeout=30))
    return False


def score_tasks(repo: Path, tasks: list[Task]) -> dict[int, dict]:
    """Objective completion evidence per task: files present, symbols defined."""
    report: dict[int, dict] = {}
    for t in tasks:
        present = [f for f in t.files if (repo / f).exists()]
        missing = [f for f in t.files if not (repo / f).exists()]
        syms_ok, syms_missing = [], []
        for s in dict.fromkeys(t.symbols):
            (syms_ok if grep_symbol(repo, s, t.files) else syms_missing).append(s)
        file_ratio = len(present) / len(t.files) if t.files else 0.0
        sym_ratio = len(syms_ok) / (len(syms_ok) + len(syms_missing)) if (syms_ok or syms_missing) else 0.0
        if t.files and t.symbols:
            complete = file_ratio == 1.0 and sym_ratio == 1.0
        elif t.files:
            complete = file_ratio == 1.0
        elif t.symbols:
            complete = sym_ratio == 1.0
        else:
            complete = False
        report[t.num] = {
            "title": t.title,
            "files_present": present,
            "files_missing": missing,
            "symbols_ok": syms_ok,
            "symbols_missing": syms_missing,
            "file_ratio": file_ratio,
            "symbol_ratio": sym_ratio,
            "complete": complete,
            "steps": len(t.steps),
        }
    return report


def detect_test_commands(repo: Path) -> dict[str, str]:
    """Infer test commands from project metadata. No hardcoded project knowledge."""
    cmds: dict[str, str] = {}

    if (repo / "pyproject.toml").is_file():
        py = None
        for cand in (".venv-win/Scripts/python.exe", ".venv/Scripts/python.exe", ".venv/bin/python"):
            if (repo / cand).is_file():
                py = cand
                break
        if py:
            cmds["backend"] = f"{py} -m pytest ./tests -q"
        elif run(["uv", "--version"], repo, 15)[0] == 0:
            cmds["backend"] = "uv run --isolated --frozen python -m pytest ./tests -q"
        else:
            cmds["backend"] = f"{Path(sys.executable).name} -m pytest ./tests -q"

    for pkg in sorted(repo.glob("*/package.json")) + ([repo / "package.json"] if (repo / "package.json").is_file() else []):
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        scripts = data.get("scripts") or {}
        sub = pkg.parent.relative_to(repo).as_posix()
        prefix = f"npm --prefix {sub} run " if sub != "." else "npm run "
        # Pick the real script name instead of assuming "test".
        for key in ("test:unit", "test", "vitest", "jest"):
            if key in scripts:
                cmds[f"frontend-test ({sub})"] = f"{prefix}{key} -- --run"
                break
        if "type-check" in scripts:
            cmds[f"frontend-types ({sub})"] = f"{prefix}type-check"
        if "lint:check" in scripts:
            cmds[f"frontend-lint ({sub})"] = f"{prefix}lint:check"
    return cmds


def summarize_test_output(name: str, out: str) -> str:
    """Condense a test run into one line, keeping failure identifiers."""
    passed = PY_PASS.search(out)
    failed = PY_FAIL.search(out)
    if passed or failed:
        parts = []
        if failed:
            parts.append(f"{failed.group(1)} failed")
        if passed:
            parts.append(f"{passed.group(1)} passed")
        line = ", ".join(parts)
        ids = PY_FAILED_ID.findall(out)
        if ids:
            line += "\n" + "\n".join(f"      FAILED {i}" for i in ids[:12])
        return line
    v = VITEST_TESTS.search(out)
    if v:
        return f"{v.group('n')} passed"
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-2:]
    return " / ".join(t.strip()[:120] for t in tail) or "<no output>"


def detect_env_pitfalls(repo: Path) -> list[str]:
    """Environment traps a fresh session would otherwise rediscover the hard way."""
    notes: list[str] = []

    for venv in sorted(repo.glob(".venv*")):
        if not venv.is_dir():
            continue
        win_py = venv / "Scripts" / "python.exe"
        nix_py = venv / "bin" / "python"
        if not win_py.is_file() and not nix_py.is_file():
            detail = ", ".join(sorted(p.name for p in venv.iterdir())[:6]) or "<empty>"
            notes.append(
                f"`{venv.name}/` 不是可用的虚拟环境（无可执行 Python，仅含 {detail}）。"
                f"不要删除或修复它，改用其他解释器。"
            )
        elif win_py.is_file():
            notes.append(f"可用解释器：`{venv.name}/Scripts/python.exe`")
        else:
            notes.append(f"可用解释器：`{venv.name}/bin/python`")

    for pkg in sorted(repo.glob("*/package.json")):
        try:
            scripts = (json.loads(pkg.read_text(encoding="utf-8", errors="replace")).get("scripts") or {})
        except (OSError, json.JSONDecodeError):
            continue
        sub = pkg.parent.relative_to(repo).as_posix()
        if "test" not in scripts:
            avail = [k for k in scripts if "test" in k.lower()]
            if avail:
                notes.append(
                    f"`{sub}` 没有 `test` 脚本；实际名称是 {', '.join('`' + a + '`' for a in avail)}。"
                    f"直接跑 `npm test` 会报 Missing script。"
                )

    lock = repo / "webui" / "yarn.lock"
    for lf in [p for p in (lock, repo / "yarn.lock", repo / "package-lock.json") if p.is_file()]:
        try:
            body = lf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        mirrors = sorted(set(re.findall(r"https://(registry\.[a-z0-9.\-]+)/", body)))
        foreign = [m for m in mirrors if "npmjs.org" not in m]
        if foreign:
            notes.append(
                f"`{lf.relative_to(repo).as_posix()}` 混入了非官方源 {', '.join(foreign)}，"
                f"可能破坏跨环境可复现性。"
            )
    return notes


def do_commit(repo: Path, protected: list[str], message: str, dry: bool) -> str:
    """Stage everything except plan-declared private files, then commit."""
    dirty = git(repo, "status", "--porcelain")
    if not dirty:
        return "工作区已干净，无需提交。"

    excludes = [f":!{p}" for p in protected]
    add_cmd = ["git", "add", "-A", "--", "."] + excludes
    if dry:
        return "DRY-RUN 将执行：\n      " + " ".join(add_cmd) + f'\n      git commit -m "{message}"'

    code, out = run(add_cmd, repo, 120)
    if code != 0:
        return f"git add 失败：{out.strip()[:300]}"
    if not git(repo, "diff", "--cached", "--name-only"):
        return "暂存区为空（改动可能全部属于受保护文件），未提交。"
    code, out = run(["git", "commit", "-m", message], repo, 120)
    if code != 0:
        return f"git commit 失败：{out.strip()[:300]}"
    return out.strip().splitlines()[0] if out.strip() else "已提交。"


def update_plan(plan_path: Path, tasks: list[Task], report: dict[int, dict], dry: bool) -> tuple[int, int]:
    """Tick every step of a task whose files and symbols are all present."""
    lines = plan_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    changed = 0
    for t in tasks:
        if not report.get(t.num, {}).get("complete"):
            continue
        for st in t.steps:
            if st.done:
                continue
            raw = lines[st.line_index]
            lines[st.line_index] = raw.replace("- [ ]", "- [x]", 1)
            changed += 1
    total = sum(len(t.steps) for t in tasks)
    if changed and not dry:
        plan_path.write_text("".join(lines), encoding="utf-8", newline="")
    return changed, total


def build_handoff(ctx: dict) -> str:
    """Render the handoff markdown. Everything here comes from inspection."""
    L: list[str] = []
    a = L.append
    a(f"# {ctx['repo_name']} 交接记录（{ctx['date']}）\n")
    a("> 本文件由 `agent-handoff.py` 自动生成。所有结论来自命令输出、文件存在性或符号检索，")
    a("> 未经验证的项目标注为「未验证」。新会话应先读本文件，再读计划文档的勾选状态。\n")

    a("## 现场\n")
    a(f"- 仓库：`{ctx['repo']}`")
    a(f"- 分支：`{ctx['branch']}`" + ("（**不是主干**）" if ctx["branch"] not in ("main", "master") else ""))
    a(f"- HEAD：`{ctx['head']}`")
    if ctx["ahead"]:
        a(f"- 本地领先远程 **{ctx['ahead']}** 个提交，**均未推送**")
    if ctx["plan_rel"]:
        a(f"- 计划文档：`{ctx['plan_rel']}`")
    a(f"- 生成时间：{ctx['now']}\n")

    a("## 第 1 步：提交快照\n")
    a("```")
    a(ctx["commit_result"])
    a("```")
    if ctx["protected"]:
        a("\n计划文档声明为用户私有、已排除在提交外的文件：")
        for p in ctx["protected"]:
            a(f"- `{p}`")
    a("")

    a("## 第 2 步：计划完成度\n")
    if ctx["report"]:
        a("| Task | 完成 / 待办 | 文件证据 | 符号证据 | 判定 |")
        a("|---|---|---|---|---|")
        for num in sorted(ctx["report"]):
            r = ctx["report"][num]
            done = ctx["done_by_task"].get(num, 0)
            todo = r["steps"] - done
            fe = f"{len(r['files_present'])}/{len(r['files_present']) + len(r['files_missing'])}" if r["files_missing"] or r["files_present"] else "—"
            se = f"{len(r['symbols_ok'])}/{len(r['symbols_ok']) + len(r['symbols_missing'])}" if r["symbols_ok"] or r["symbols_missing"] else "—"
            verdict = "完成" if r["complete"] else ("部分" if r["files_present"] or r["symbols_ok"] else "**未开始**")
            title = r["title"].split(":", 1)[-1].strip()[:38]
            a(f"| {num} {title} | {done} / {todo} | {fe} | {se} | {verdict} |")
        a(f"\n合计 **{ctx['ticked']}** 步已勾选 / 共 **{ctx['total_steps']}** 步。\n")

        gaps = [(n, r) for n, r in sorted(ctx["report"].items()) if r["files_missing"] or r["symbols_missing"]]
        if gaps:
            a("### 缺口明细\n")
            for num, r in gaps:
                a(f"**Task {num}** — {r['title'].split(':', 1)[-1].strip()}")
                for f in r["files_missing"]:
                    a(f"- 缺文件 `{f}`")
                for s in r["symbols_missing"]:
                    a(f"- 缺符号 `{s}`")
                a("")
    else:
        a("未找到计划文档，跳过完成度回填。\n")

    a("## 第 3 步：实测结果\n")
    if ctx["test_results"]:
        for name, line in ctx["test_results"].items():
            a(f"**{name}** — `{ctx['test_commands'][name]}`")
            a("```")
            a(line)
            a("```")
    else:
        a("本次未执行测试（使用了 `--skip-tests`，或未识别出测试命令）。")
    a("")

    if ctx["pitfalls"]:
        a("## 环境注意事项\n")
        for n in ctx["pitfalls"]:
            a(f"- {n}")
        a("")

    if ctx["recent_commits"]:
        a("## 最近提交\n```")
        a(ctx["recent_commits"])
        a("```\n")

    if ctx["vitals"]:
        a("## 会话体征\n")
        a("按转录体积与致命错误标记排序。判据来自本机 54 个 Claude + 54 个 Codex 转录的实测分布：")
        a("250 KB 以下无一出现致命错误，8 MB 以上全部出现过。\n")
        a("| 智能体 | 转录 | 体积 | 致命 | 工具错误 | 判定 |")
        a("|---|---|---|---|---|---|")
        for r in ctx["vitals"][:8]:
            label = {"critical": "**立刻交接**", "high": "尽快交接", "watch": "留意", "ok": "健康"}[r["band"]]
            a(f"| {r['agent']} | `{r['file'][:14]}` | {r['mb']:.1f} MB | {r['fatal']} | {r['errors']} | {label} |")
        worst = ctx["vitals"][0]
        if worst["band"] in ("critical", "high"):
            a(f"\n最紧迫：{worst['agent']} 的 `{worst['file'][:14]}`（{worst['mb']:.1f} MB）。{worst['advice']}")
        a("")

    a("## 新会话开场提示词\n")
    a("直接把下面整段粘贴进新会话的输入框发送。**不要**把本文件内容一起粘贴——")
    a("它已在仓库里，让智能体自己读，以免重演上下文超限。\n")
    a("```text")
    a(ctx["prompt"])
    a("```")
    return "\n".join(L) + "\n"


def build_prompt(ctx: dict) -> str:
    L: list[str] = []
    a = L.append
    a(f"接续 {ctx['repo_name']}。仓库 {ctx['repo']}，分支 {ctx['branch']}，HEAD {ctx['head_sha']}。")
    a("")
    if ctx["plan_rel"] and ctx["handoff_rel"]:
        a(f"先读 {ctx['plan_rel']} 的勾选状态和 {ctx['handoff_rel']}，")
        a(f"不要重做已完成的 {ctx['ticked']} 步。")
    elif ctx["handoff_rel"]:
        a(f"先读 {ctx['handoff_rel']} 了解现场，不要重做已完成的工作。")
    a("")
    nxt = ctx["next_tasks"]
    if nxt:
        a("优先级：" + " → ".join(f"Task {n}" for n in nxt[:4]) + "。")
    if ctx["failing"]:
        a("先修下列失败项，让测试全绿：")
        for f in ctx["failing"][:6]:
            a(f"  {f}")
    a("")
    if ctx["pitfalls"]:
        a("环境：")
        for p in ctx["pitfalls"][:4]:
            a("  - " + re.sub(r"[`*]", "", p))
    return "\n".join(x for x in L if x is not None)


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="agent-handoff",
        description="把仓库当前状态固化成可无损接续的交接现场（提交快照 + 回填计划 + 生成交接文件）。",
    )
    ap.add_argument("repo", nargs="?", default=".", help="仓库路径，默认当前目录")
    ap.add_argument("--plan", help="计划文档路径；省略则自动探测最新的含复选框任务文档")
    ap.add_argument("--out", help="交接文件输出路径；默认与计划文档同目录")
    ap.add_argument("-m", "--message", help="提交信息；省略则自动生成")
    ap.add_argument("--no-commit", action="store_true", help="不提交，只分析并生成交接文件")
    ap.add_argument("--skip-tests", action="store_true", help="不跑测试（快速模式）")
    ap.add_argument("--test-timeout", type=int, default=900, help="单条测试命令超时秒数，默认 900")
    ap.add_argument("--vitals", action="store_true", help="只体检本机会话转录并退出，不碰仓库")
    ap.add_argument("--no-vitals", action="store_true", help="跳过会话体检（交接文件里不含体征表）")
    ap.add_argument("--dry-run", action="store_true", help="全程只打印将要做什么，不写任何文件")
    args = ap.parse_args()

    if args.vitals:
        rows = scan_session_vitals()
        if not rows:
            print("未找到任何会话转录（~/.claude/projects 与 ~/.codex/sessions 都不存在）。")
            return 0
        print(f"{'agent':<13} {'transcript':<16} {'size':>8} {'fatal':>6} {'errors':>7}  verdict")
        print("-" * 72)
        for r in rows:
            label = {"critical": "立刻交接", "high": "尽快交接", "watch": "留意", "ok": "健康"}[r["band"]]
            print(
                f"{r['agent']:<13} {r['file'][:14]:<16} {r['mb']:>6.1f}MB "
                f"{r['fatal']:>6} {r['errors']:>7}  {label}"
            )
        worst = rows[0]
        if worst["band"] in ("critical", "high"):
            print(f"\n{worst['agent']} 的 {worst['file'][:14]}：{worst['advice']}")
            print("在那个会话所属仓库运行 agent-handoff . 固化现场。")
        else:
            print("\n无高危会话。")
        return 0

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"错误：{repo} 不是目录", file=sys.stderr)
        return 2
    if not (repo / ".git").exists():
        print(f"错误：{repo} 不是 git 仓库。交接依赖 git 元数据。", file=sys.stderr)
        return 2

    print(f"[1/6] 读取仓库元数据 — {repo}")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "<detached>"
    head = git(repo, "log", "--oneline", "-1") or "<no commits>"
    upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    ahead = ""
    if upstream:
        ahead = git(repo, "rev-list", "--count", f"{upstream}..HEAD")
    else:
        for ref in ("origin/main", "origin/master"):
            if git(repo, "rev-parse", "--verify", ref):
                ahead = git(repo, "rev-list", "--count", f"{ref}..HEAD")
                break

    print("[2/6] 定位并解析计划文档")
    plan_path = find_plan(repo, args.plan)
    tasks: list[Task] = []
    protected: list[str] = []
    if plan_path:
        text = plan_path.read_text(encoding="utf-8", errors="replace")
        tasks, protected = parse_plan(text)
        print(f"      {plan_path.relative_to(repo).as_posix()} — {len(tasks)} 个任务，"
              f"{sum(len(t.steps) for t in tasks)} 步，{len(protected)} 个受保护文件")
    else:
        print("      未找到计划文档（跳过回填；交接文件仍会生成）")

    print("[3/6] 按文件与符号证据评估完成度")
    report = score_tasks(repo, tasks) if tasks else {}

    print("[4/6] 提交快照")
    msg = args.message or f"chore: 会话交接快照 {datetime.now():%Y-%m-%d %H:%M}"
    commit_result = "已跳过（--no-commit）" if args.no_commit else do_commit(repo, protected, msg, args.dry_run)
    print(f"      {commit_result.splitlines()[0]}")

    ticked, total_steps = (0, sum(len(t.steps) for t in tasks))
    if tasks:
        ticked_now, total_steps = update_plan(plan_path, tasks, report, args.dry_run)
        ticked = sum(1 for t in tasks for s in t.steps if s.done) + ticked_now
        print(f"      计划回填 +{ticked_now} 步，共 {ticked}/{total_steps} 已勾选")

    done_by_task: dict[int, int] = {}
    for t in tasks:
        n = sum(1 for s in t.steps if s.done)
        if report.get(t.num, {}).get("complete"):
            n = len(t.steps)
        done_by_task[t.num] = n

    print("[5/6] 运行测试取证")
    test_commands = detect_test_commands(repo)
    test_results: dict[str, str] = {}
    failing: list[str] = []
    if args.skip_tests:
        print("      已跳过（--skip-tests）")
    else:
        for name, cmd in test_commands.items():
            print(f"      {name}: {cmd}")
            _, out = run(cmd, repo, args.test_timeout)
            line = summarize_test_output(name, out)
            test_results[name] = line
            print(f"        -> {line.splitlines()[0]}")
            failing += [f"FAILED {i}" for i in PY_FAILED_ID.findall(out)[:6]]

    pitfalls = detect_env_pitfalls(repo)
    next_tasks = [n for n in sorted(report) if not report[n]["complete"]]
    vitals = [] if args.no_vitals else scan_session_vitals()
    if vitals:
        worst = vitals[0]
        print(f"      会话体征：最紧迫 {worst['agent']} {worst['file'][:14]} "
              f"{worst['mb']:.1f} MB fatal={worst['fatal']} -> {worst['band']}")

    print("[6/6] 生成交接文件")
    out_dir = plan_path.parent if plan_path else (repo / "docs")
    out_path = Path(args.out) if args.out else out_dir / f"{datetime.now():%Y-%m-%d}-handoff.md"
    if not out_path.is_absolute():
        out_path = repo / out_path

    ctx = {
        "repo": repo.as_posix(),
        "repo_name": repo.name,
        "branch": branch,
        "head": head,
        "head_sha": head.split()[0] if head and head != "<no commits>" else head,
        "ahead": ahead if ahead and ahead != "0" else "",
        "plan_rel": plan_path.relative_to(repo).as_posix() if plan_path else "",
        "handoff_rel": out_path.relative_to(repo).as_posix() if out_path.is_relative_to(repo) else out_path.as_posix(),
        "date": f"{datetime.now():%Y-%m-%d}",
        "now": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        "commit_result": commit_result,
        "protected": protected,
        "report": report,
        "done_by_task": done_by_task,
        "ticked": ticked,
        "total_steps": total_steps,
        "test_commands": test_commands,
        "test_results": test_results,
        "failing": failing,
        "pitfalls": pitfalls,
        "next_tasks": next_tasks,
        "vitals": vitals,
        "recent_commits": git(repo, "log", "--oneline", "-5"),
    }
    ctx["prompt"] = build_prompt(ctx)
    body = build_handoff(ctx)

    if args.dry_run:
        print(f"\nDRY-RUN：将写入 {out_path}（{len(body)} 字节），内容预览：\n")
        print("\n".join(body.splitlines()[:40]))
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8", newline="")
    print(f"      {out_path}")

    if not args.no_commit:
        paths = [out_path]
        if plan_path:
            paths.append(plan_path)
        rels = [p.relative_to(repo).as_posix() for p in paths if p.is_relative_to(repo)]
        if rels:
            run(["git", "add", *rels], repo, 60)
            if git(repo, "diff", "--cached", "--name-only"):
                run(["git", "commit", "-m", "docs: 回填计划完成状态并记录交接现场"], repo, 60)

    print("\n" + "=" * 68)
    print("新会话开场提示词（复制下面整段）")
    print("=" * 68)
    print(ctx["prompt"])
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
