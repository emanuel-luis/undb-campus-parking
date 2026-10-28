"""The dbt holidays seed and the generator's holiday list must hold the same dates."""

import csv
from datetime import date
from pathlib import Path

from turnstile_generator.demand import HOLIDAYS_2026

SEED = Path(__file__).resolve().parents[2] / "backend" / "warehouse" / "seeds" / "holidays.csv"


def test_the_dbt_seed_mirrors_the_generator_holidays() -> None:
    with SEED.open(encoding="utf-8", newline="") as f:
        rows = {date.fromisoformat(r["holiday_date"]): r["holiday_name"] for r in csv.DictReader(f)}
    assert rows == HOLIDAYS_2026
