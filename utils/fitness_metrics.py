from __future__ import annotations

from typing import Any

POINTS_WORKOUT = 10
POINTS_YOGA = 10
POINTS_MEDITATION = 5


def seconds_to_minutes(seconds: int) -> int:
    s = int(seconds or 0)
    if s <= 0:
        return 0
    # Keep UX-friendly integer minutes while avoiding silent truncation.
    return max(1, int(round(s / 60)))


def run_effective_seconds(run: Any) -> int:
    reported = int(getattr(run, "total_seconds", 0) or 0)
    results = getattr(run, "exercise_results", None) or []

    by_sets_seconds = 0
    for item in results:
        if hasattr(item, "model_dump"):
            row = item.model_dump()
        elif isinstance(item, dict):
            row = item
        else:
            row = {}

        try:
            sec = int(row.get("seconds_done", 0) or 0)
        except Exception:
            sec = 0
        if sec > 0:
            by_sets_seconds += sec

    # Prefer the larger value to avoid undercounting when one source is partial.
    return max(reported, by_sets_seconds, 0)


def build_metrics_block(
    *,
    total_seconds: int = 0,
    total_calories: float | None = None,
    total_points: int | None = None,
    total_sets: int | None = None,
    total_reps: int | None = None,
    total_intervals: int | None = None,
    timed_intervals: int | None = None,
    timed_intervals_seconds: int | None = None,
    rest_between_sets_seconds: int | None = None,
) -> dict[str, int | float]:
    metrics: dict[str, int | float] = {
        "total_seconds": int(total_seconds or 0),
        "total_minutes": seconds_to_minutes(int(total_seconds or 0)),
    }
    if total_calories is not None:
        metrics["total_calories"] = float(total_calories)
    if total_points is not None:
        metrics["total_points"] = int(total_points)
    if total_sets is not None:
        metrics["total_sets"] = int(total_sets)
    if total_reps is not None:
        metrics["total_reps"] = int(total_reps)
    if total_intervals is not None:
        metrics["total_intervals"] = int(total_intervals)
    if timed_intervals is not None:
        metrics["timed_intervals"] = int(timed_intervals)
    if timed_intervals_seconds is not None:
        metrics["timed_intervals_seconds"] = int(timed_intervals_seconds)
    if rest_between_sets_seconds is not None:
        metrics["rest_between_sets_seconds"] = int(rest_between_sets_seconds)
    return metrics
