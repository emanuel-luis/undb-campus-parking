"""Synthetic demand: arrivals per minute from local time, weekday, holidays and semester start.

Everything here is invented for the demo. The curve, the factors and the holiday list are
assumptions, not measurements of the UNDB campus.
"""

import math
from datetime import date, timedelta

# Demand factor by weekday (Monday = 0).
WEEKDAY_FACTOR = (1.0, 1.0, 1.0, 1.0, 1.0, 0.35, 0.05)
HOLIDAY_FACTOR = 0.1
QUIET_WEEK_FACTOR = 0.25

# Approximate 2026 national and São Luís holidays, mirrored by the dbt seed
# backend/warehouse/seeds/holidays.csv (a test compares them). Not an official calendar.
HOLIDAYS_2026: dict[date, str] = {
    date(2026, 1, 1): "Confraternização Universal",
    date(2026, 2, 16): "Carnaval",
    date(2026, 2, 17): "Carnaval",
    date(2026, 4, 3): "Sexta-feira Santa",
    date(2026, 4, 21): "Tiradentes",
    date(2026, 5, 1): "Dia do Trabalho",
    date(2026, 6, 4): "Corpus Christi",
    date(2026, 7, 28): "Adesão do Maranhão à Independência",
    date(2026, 9, 7): "Independência do Brasil",
    date(2026, 9, 8): "Aniversário de São Luís",
    date(2026, 10, 12): "Nossa Senhora Aparecida",
    date(2026, 11, 2): "Finados",
    date(2026, 11, 15): "Proclamação da República",
    date(2026, 11, 20): "Consciência Negra",
    date(2026, 12, 25): "Natal",
}

# (center hour, spread in hours, weight): the morning, lunch and evening peaks.
_PEAKS = ((7.25, 0.9, 1.0), (13.0, 0.8, 0.8), (18.25, 1.0, 0.9))


def _intensity(hour: float) -> float:
    base = 0.04 if 6 <= hour < 22 else 0.002  # night is near zero
    return base + sum(w * math.exp(-0.5 * ((hour - mu) / sd) ** 2) for mu, sd, w in _PEAKS)


_RAW = [_intensity((m + 0.5) / 60) for m in range(1440)]
# Share of a day's arrivals that fall in each minute of the local day (sums to 1).
MINUTE_SHARE: tuple[float, ...] = tuple(v / sum(_RAW) for v in _RAW)


def day_factor(d: date, semester_start: date) -> float:
    """Demand factor of a local date: holiday, weekday, and the quiet week before the semester."""
    factor = HOLIDAY_FACTOR if d in HOLIDAYS_2026 else WEEKDAY_FACTOR[d.weekday()]
    if semester_start - timedelta(days=7) <= d < semester_start:
        factor *= QUIET_WEEK_FACTOR
    return factor
