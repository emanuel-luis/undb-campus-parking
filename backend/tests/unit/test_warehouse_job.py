"""The warehouse job without dbt: commands, environment, run_results parsing, failures and the
subprocess lifecycle (with a stand-in Python process)."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from campus_parking.jobs import warehouse
from campus_parking.jobs.runs import RunResult
from tests.conftest import make_settings

SECRET = "s3cr3t-pw"
URL = f"postgresql+asyncpg://parking:{SECRET}@db.internal:6543/parking"


def test_env_is_derived_from_the_database_url() -> None:
    env = warehouse.dbt_env(URL, {"PATH": "/bin"})
    assert env == {
        "PATH": "/bin",
        "DBT_PG_HOST": "db.internal",
        "DBT_PG_PORT": "6543",
        "DBT_PG_USER": "parking",
        "DBT_PG_PASSWORD": SECRET,
        "DBT_PG_DBNAME": "parking",
    }
    defaults = warehouse.dbt_env("postgresql+asyncpg:///parking", {})
    assert (defaults["DBT_PG_HOST"], defaults["DBT_PG_PORT"]) == ("localhost", "5432")


def test_commands_never_carry_the_password(tmp_path: Path) -> None:
    settings = make_settings(database_url=URL, dbt_project_dir=tmp_path)
    incremental = warehouse.dbt_commands(settings, "incremental")
    full = warehouse.dbt_commands(settings, "full")
    assert [c[:2] for c in incremental] == [["build", "--project-dir"]]
    assert [c[:2] for c in full] == [["source", "freshness"], ["build", "--full-refresh"]]
    for command in incremental + full:
        assert str(tmp_path.resolve()) in command
        assert json.dumps({"timezone": "America/Fortaleza"}) in command
        assert SECRET not in " ".join(command)


RUN_RESULTS = {
    "results": [
        {"unique_id": "model.p.a", "status": "success", "adapter_response": {"rows_affected": 5}},
        {"unique_id": "model.p.b", "status": "success", "adapter_response": {"rows_affected": -1}},
        {"unique_id": "seed.p.holidays", "status": "success", "adapter_response": {}},
        {"unique_id": "test.p.t1", "status": "pass"},
        {"unique_id": "test.p.t2", "status": "fail"},
        {"unique_id": "unit_test.p.u", "status": "pass"},
    ]
}


def test_summarize_counts_by_type_and_lists_failures() -> None:
    assert warehouse.summarize(RUN_RESULTS) == {
        "nodes": {
            "model": {"success": 2},
            "seed": {"success": 1},
            "test": {"pass": 1, "fail": 1},
            "unit_test": {"pass": 1},
        },
        "rows_affected": 5,
        "failed": ["test.p.t2"],
    }


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [([], "pass"), (["pass", "warn"], "warn"), (["warn", "runtime error"], "error")],
)
def test_freshness_status_is_the_worst_one(statuses: list[str], expected: str) -> None:
    sources = {"results": [{"status": s} for s in statuses]}
    assert warehouse.freshness_status(sources) == expected


def fake_dbt(
    monkeypatch: pytest.MonkeyPatch, project: Path, results: dict[str, Any] | None, code: int = 0
) -> list[list[str]]:
    calls: list[list[str]] = []

    async def run(args: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
        calls.append(args)
        assert cwd == project.resolve()
        assert env["DBT_PG_PASSWORD"] == SECRET
        target = project / "target"
        target.mkdir(exist_ok=True)
        if args[0] == "source":
            (target / "sources.json").write_text(json.dumps({"results": [{"status": "warn"}]}))
        elif results is not None:
            (target / "run_results.json").write_text(json.dumps(results))
        return code, "line one\nlast words"

    monkeypatch.setattr(warehouse, "run_dbt", run)
    return calls


async def test_full_run_records_freshness_and_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok = {"results": [r for r in RUN_RESULTS["results"] if r["status"] != "fail"]}
    calls = fake_dbt(monkeypatch, tmp_path, ok)
    result = RunResult()
    settings = make_settings(database_url=URL, dbt_project_dir=tmp_path)
    await warehouse.run_warehouse(settings, "full", result)
    assert [c[0] for c in calls] == ["source", "build"]
    assert result.metrics["freshness"] == "warn"
    assert result.metrics["mode"] == "full"
    assert result.rows_written == 5
    assert result.metrics["failed"] == []


async def test_a_failed_node_fails_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dbt(monkeypatch, tmp_path, RUN_RESULTS, code=1)
    result = RunResult()
    settings = make_settings(database_url=URL, dbt_project_dir=tmp_path)
    with pytest.raises(warehouse.DbtFailed, match=r"exited 1; failed: test\.p\.t2") as info:
        await warehouse.run_warehouse(settings, "incremental", result)
    assert SECRET not in str(info.value)
    assert result.metrics["nodes"]["test"] == {"pass": 1, "fail": 1}


async def test_a_crash_without_run_results_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dbt(monkeypatch, tmp_path, None, code=2)
    settings = make_settings(database_url=URL, dbt_project_dir=tmp_path)
    with pytest.raises(warehouse.DbtFailed, match="no run_results; last line: last words"):
        await warehouse.run_warehouse(settings, "incremental", RunResult())


async def test_run_dbt_returns_the_exit_code_and_output_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = "import sys; print('\\n'.join(map(str, range(30)))); sys.exit(3)"
    monkeypatch.setattr(warehouse, "DBT", [sys.executable, "-c", script])
    code, tail = await warehouse.run_dbt(["build"], tmp_path, {"PATH": ""})
    assert code == 3
    assert tail.splitlines() == [str(i) for i in range(10, 30)]


async def test_cancelling_terminates_dbt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = "import time; time.sleep(60)"
    monkeypatch.setattr(warehouse, "DBT", [sys.executable, "-c", script])
    started: list[Any] = []
    real = asyncio.create_subprocess_exec

    async def spy(*args: Any, **kwargs: Any) -> Any:
        proc = await real(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    task = asyncio.ensure_future(warehouse.run_dbt([], tmp_path, {"PATH": ""}))
    while not started:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert started[0].returncode is not None
