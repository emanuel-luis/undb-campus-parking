"""Pure availability figures."""


def lot_figures(capacity: int | None, occupied: int) -> tuple[int | None, float | None, bool]:
    """(free, occupancy_pct, over_capacity). `occupied` is stored exact and never clamped; the
    figures shown to people are: free = max(capacity - occupied, 0), pct rounded to 1 decimal."""
    if capacity is None:
        return None, None, False
    return max(capacity - occupied, 0), round(occupied / capacity * 100, 1), occupied > capacity
