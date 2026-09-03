"""日志目录必须落在 `DATA_PATH` 之下（需求 18.2 / 18.3）。

`docs/QQ_ONEBOT_OPERATIONS.md` 的数据目录清单开头写着「只要挂载 `DATA_PATH`
这一个目录，重启就不会丢状态」。但 `kirara_ai/logger.py` 用的是**裸相对路径**
`logs`，它落在进程工作目录下、不在 `DATA_PATH` 里、也没有任何 compose 卷挂它。

后果正好落在最需要日志的时刻：`docker compose down` 之后，运维按第八节验收矩阵
去翻「日志证据」，而那批日志刚刚随容器一起消失了。文档在这一点上给出了与事实
相反的承诺，这比不写更糟。

`KIRARA_LOG_DIR` 保留为显式覆盖出口：已经把日志接到外部收集器的部署不该被强改。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _log_dir_in_subprocess(env: dict[str, str]) -> str:
    """在干净子进程里问 `logger` 模块它把日志写到哪。

    必须用子进程：`logger.py` 在导入期就创建目录并注册 sink，
    当前进程早已导入过它，改环境变量对它没有影响。
    """
    code = (
        "import json, kirara_ai.logger as L;"
        "print(json.dumps({'log_dir': str(L.LOG_DIR)}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **env},
        check=False,
        # 导入一个模块不该要几十秒。给上界是为了让「卡住」变成一条明确的失败，
        # 而不是让整个测试进程挂在这里等——后者在 CI 上表现为超时打断，
        # 报告里看不出是哪个用例。
        timeout=120,
    )
    if result.returncode != 0:
        # 连 stdout 一起报：导入期失败的诊断可能落在任一个流上
        # （`ensure_data_directories` 抛的是 RuntimeError，而 loguru 的早期
        # sink 写 stdout），只报 stderr 会得到一条空信息——那时既看不出原因
        # 也无从复现。这条用例在一次全量运行里失败过一次，而单独跑十次全绿，
        # 当时留下的就是一条没有内容的失败。
        pytest.fail(
            "导入 kirara_ai.logger 失败"
            f"（exit={result.returncode}）\n"
            f"--- stderr ---\n{result.stderr[-2000:]}\n"
            f"--- stdout ---\n{result.stdout[-2000:]}"
        )

    import json

    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)["log_dir"]
    pytest.fail(f"未能从输出里解析 LOG_DIR：{result.stdout[-2000:]}")


def test_logs_live_under_the_mounted_data_path(tmp_path: Path):
    """日志目录必须在 `DATA_PATH` 之下，否则挂一个卷保不住它。"""
    data_path = tmp_path / "data"
    log_dir = Path(
        _log_dir_in_subprocess({"DATA_PATH": str(data_path)})
    ).resolve()

    assert log_dir.is_relative_to(data_path.resolve()), (
        f"日志写到了 {log_dir}，它不在 DATA_PATH（{data_path}）下面；"
        "文档承诺「只挂 DATA_PATH 就不丢状态」，这条承诺会因此变成假的"
    )


def test_the_log_directory_is_actually_created(tmp_path: Path):
    """目录要真的建出来：只算出路径不建目录，第一条日志就会失败。"""
    data_path = tmp_path / "data"
    log_dir = Path(_log_dir_in_subprocess({"DATA_PATH": str(data_path)}))

    assert log_dir.is_dir()


def test_an_explicit_override_is_honored(tmp_path: Path):
    """`KIRARA_LOG_DIR` 可覆盖：已接外部日志收集器的部署不该被强改路径。"""
    data_path = tmp_path / "data"
    custom = tmp_path / "elsewhere" / "kirara-logs"

    log_dir = Path(
        _log_dir_in_subprocess(
            {"DATA_PATH": str(data_path), "KIRARA_LOG_DIR": str(custom)}
        )
    ).resolve()

    assert log_dir == custom.resolve()
    assert log_dir.is_dir()


def test_the_operations_doc_lists_the_log_directory():
    """数据目录清单必须包含日志这一行。

    需求 18.2 把日志列为必须写入部署文档的项目之一。清单漏掉它，
    运维就不知道日志在哪、要不要备份、升级时会不会被清掉。
    """
    doc = (PROJECT_ROOT / "docs" / "QQ_ONEBOT_OPERATIONS.md").read_text(encoding="utf-8")
    inventory_start = doc.find("## 三、数据目录清单")
    assert inventory_start != -1, "数据目录清单章节不存在"
    inventory = doc[inventory_start : doc.find("## 四、", inventory_start)]

    assert "logs" in inventory, "数据目录清单里没有日志目录这一行"
    # 同一行要说清保留策略，否则「要不要备份」仍然没有答案。
    assert "7" in inventory or "轮转" in inventory


def test_the_docker_ignore_still_excludes_logs_from_the_image():
    """日志进 DATA_PATH 之后，仍然不得进镜像构建上下文。"""
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    patterns = {
        line.strip()
        for line in dockerignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "*.log" in patterns and "**/*.log" in patterns
    assert "data/logs" in patterns
