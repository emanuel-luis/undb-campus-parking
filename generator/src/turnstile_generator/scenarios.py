"""Scenarios: multipliers over local time windows, applied on top of the demand profile."""

from typing import Literal

Scenario = Literal["normal", "rush_hour", "quiet"]

# (start minute of day, end minute of day, multiplier) for the rush windows.
_RUSH_WINDOWS = ((6 * 60 + 30, 9 * 60, 2.5), (12 * 60, 14 * 60, 2.0), (17 * 60 + 30, 19 * 60, 2.5))


def multiplier(scenario: Scenario, minute_of_day: int) -> float:
    if scenario == "quiet":
        return 0.3
    if scenario == "rush_hour":
        for start, end, factor in _RUSH_WINDOWS:
            if start <= minute_of_day < end:
                return factor
    return 1.0
