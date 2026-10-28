from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect

from alembic import command
from campus_parking import cli


def test_migrate_wires_config_and_revision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[Any, str]] = []
    monkeypatch.setattr(cli.command, "upgrade", lambda cfg, rev: calls.append((cfg, rev)))
    monkeypatch.chdir(tmp_path)  # works from any working directory
    assert cli.main(["migrate"]) == 0
    assert cli.main(["migrate", "--revision", "0001"]) == 0
    (cfg, rev), (_, rev2) = calls
    assert (rev, rev2) == ("head", "0001")
    script = Path(cfg.get_main_option("script_location"))
    assert script.is_absolute()
    assert (script / "env.py").is_file()
    assert (script / "versions" / "0001_registry.py").is_file()


def test_migrate_config_from_env_and_arg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ini = tmp_path / "alt" / "alembic.ini"
    ini.parent.mkdir()
    ini.write_text("[alembic]\nscript_location = alembic\n", encoding="utf-8")
    assert cli.alembic_config(ini).get_main_option("script_location") == str(ini.parent / "alembic")
    monkeypatch.setenv("ALEMBIC_INI", str(ini))
    assert cli.alembic_config().config_file_name == str(ini.resolve())


def test_migrate_upgrade_and_downgrade_on_sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "parking.db"
    monkeypatch.setenv("PARKING_DATABASE_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["migrate"]) == 0
    sync = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        tables = set(inspect(sync).get_table_names())
        assert {"lots", "lot_capacity_history", "gates", "students"} <= tables
        gate_checks = inspect(sync).get_check_constraints("gates")
        assert [c["name"] for c in gate_checks] == ["ck_gates_direction_valid"]
        command.downgrade(cli.alembic_config(), "base")
        assert set(inspect(sync).get_table_names()) == {"alembic_version"}
    finally:
        sync.dispose()
