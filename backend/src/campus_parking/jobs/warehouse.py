"""The warehouse job: dbt as a subprocess, its run_results.json summarised into the run row."""

import asyncio
import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.engine import make_url

from campus_parking.core.settings import Settings
from campus_parking.jobs.runs import RunResult

logger = logging.getLogger(__name__)

WarehouseMode = Literal["incremental", "full"]
MODES: tuple[WarehouseMode, ...] = ("incremental", "full")
TERMINATE_GRACE_S = 10.0
FAILED = {"error", "fail", "runtime error"}
OUTPUT_TAIL = 20
# dbt-core has no `python -m dbt`; its CLI module runs as a script.
DBT = [sys.executable, "-m", "dbt.cli.main"]


class DbtFailed(RuntimeError):
    """dbt exited non-zero or reported a failed node."""


def dbt_env(database_url: str, base: Mapping[str, str]) -> dict[str, str]:
    """The environment for dbt: `base` plus DBT_PG_* taken from PARKING_DATABASE_URL."""
    url = make_url(database_url)
    return {
        **base,
        "DBT_PG_HOST": url.host or "localhost",
        "DBT_PG_PORT": str(url.port or 5432),
        "DBT_PG_USER": url.username or "",
        "DBT_PG_PASSWORD": url.password or "",
        "DBT_PG_DBNAME": url.database or "",
    }


def dbt_commands(settings: Settings, mode: WarehouseMode) -> list[list[str]]:
    """The dbt invocations of a run, in order (each without the interpreter prefix)."""
    project = str(settings.dbt_project_dir.resolve())
    common = [
        "--project-dir",
        project,
        "--profiles-dir",
        project,
        "--vars",
        json.dumps({"timezone": settings.timezone}),
    ]
    if mode == "full":
        return [["source", "freshness", *common], ["build", "--full-refresh", *common]]
    return [["build", *common]]


def summarize(run_results: Mapping[str, Any]) -> dict[str, Any]:
    """Counts by resource type and status, rows affected, and the failed nodes."""
    by_type: dict[str, Counter[str]] = {}
    rows = 0
    failed: list[str] = []
    for node in run_results.get("results", []):
        unique_id = str(node.get("unique_id", ""))
        status = str(node.get("status", ""))
        kind = unique_id.split(".", 1)[0] or "unknown"
        by_type.setdefault(kind, Counter())[status] += 1
        affected = (node.get("adapter_response") or {}).get("rows_affected")
        if kind == "model" and isinstance(affected, int) and affected > 0:
            rows += affected
        if status in FAILED:
            failed.append(unique_id)
    return {
        "nodes": {kind: dict(counts) for kind, counts in sorted(by_type.items())},
        "rows_affected": rows,
        "failed": failed,
    }


def freshness_status(sources: Mapping[str, Any]) -> str:
    """The worst status in sources.json: pass < warn < error (runtime error counts as error)."""
    rank = {"pass": 0, "warn": 1, "error": 2, "runtime error": 2}
    worst = "pass"
    for node in sources.get("results", []):
        status = str(node.get("status", "error"))
        if rank.get(status, 2) > rank[worst]:
            worst = "error" if status == "runtime error" else status
    return worst


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def run_dbt(args: list[str], cwd: Path, env: Mapping[str, str]) -> tuple[int, str]:
    """Run dbt; terminate it (then kill) when the task is cancelled, e.g. on SIGTERM."""
    proc = await asyncio.create_subprocess_exec(
        *DBT,
        *args,
        cwd=cwd,
        env=dict(env),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await proc.communicate()
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), TERMINATE_GRACE_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
    tail = out.decode("utf-8", "replace").strip().splitlines()[-OUTPUT_TAIL:]
    return proc.returncode or 0, "\n".join(tail)


async def run_warehouse(settings: Settings, mode: WarehouseMode, result: RunResult) -> None:
    """dbt build (incremental) or source freshness + build --full-refresh (full).

    Freshness is recorded, never fatal: the campus is empty at night, so the daily run at 00:30
    always sees events older than the error threshold. Any failed build node fails the run.
    """
    project = settings.dbt_project_dir.resolve()
    env = dbt_env(settings.database_url, os.environ)
    result.metrics["mode"] = mode
    for args in dbt_commands(settings, mode):
        code, tail = await run_dbt(args, project, env)
        if args[0] == "source":
            sources = _read_json(project / "target" / "sources.json")
            result.metrics["freshness"] = freshness_status(sources) if sources else "unknown"
            continue
        run_results = _read_json(project / "target" / "run_results.json")
        summary = summarize(run_results) if run_results else {"failed": []}
        result.metrics.update(summary)
        result.rows_written = summary.get("rows_affected")
        if code != 0 or summary["failed"]:
            failed = ", ".join(summary["failed"]) or "no run_results"
            logger.error("dbt failed", extra={"exit_code": code, "output": tail})
            last = tail.splitlines()[-1] if tail else ""
            raise DbtFailed(f"dbt {args[0]} exited {code}; failed: {failed}; last line: {last}")
