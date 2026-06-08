from __future__ import annotations

import math
from typing import Any, Optional


def estimate_reps_duration_seconds(target_reps: int) -> int:
    reps = max(1, int(target_reps or 1))
    return max(15, min(180, reps * 6))


def apply_uniform_rest_seconds(
    sets_payload: list[dict[str, Any]],
    rest_seconds: Optional[int],
) -> list[dict[str, Any]]:
    if rest_seconds is None:
        return [dict(item or {}) for item in (sets_payload or [])]

    normalized_rest = max(0, int(rest_seconds))
    updated: list[dict[str, Any]] = []
    for set_row in list(sets_payload or []):
        row = dict(set_row or {})
        row["rest_seconds_after"] = normalized_rest
        reps_rows: list[dict[str, Any]] = []
        for rep_row in list(row.get("reps") or []):
            reps_rows.append(dict(rep_row or {}))
        row["reps"] = reps_rows
        updated.append(row)
    return updated


def summarize_sets_payload(
    sets_payload: list[dict[str, Any]],
    *,
    fallback_mode: str = "reps",
) -> dict[str, Any]:
    normalized_sets = [dict(item or {}) for item in list(sets_payload or [])]

    total_sets = len(normalized_sets)
    total_intervals = 0
    total_reps = 0
    active_seconds = 0
    timed_intervals = 0
    timed_intervals_seconds = 0
    rest_between_sets_seconds = 0
    set_summaries: list[dict[str, Any]] = []

    for set_index, set_row in enumerate(normalized_sets):
        reps_rows = [dict(item or {}) for item in list(set_row.get("reps") or [])]
        total_intervals += len(reps_rows)
        mode_value = str(set_row.get("mode") or fallback_mode or "reps")
        set_target_reps = 0
        set_duration_seconds = 0

        for rep_index, rep_row in enumerate(reps_rows, start=1):
            rep_mode = str(rep_row.get("mode") or mode_value or fallback_mode or "reps")
            rep_row["rep_no"] = int(rep_row.get("rep_no", rep_index) or rep_index)
            rep_row["mode"] = rep_mode

            target_reps = rep_row.get("target_reps", rep_row.get("target"))
            target_duration_seconds = rep_row.get(
                "target_duration_seconds",
                rep_row.get("duration_seconds"),
            )

            if target_reps is not None:
                target_reps = max(1, int(target_reps))
                rep_row["target_reps"] = target_reps
                rep_row["target"] = target_reps
                total_reps += target_reps
                set_target_reps += target_reps
                if rep_mode == "reps":
                    estimated = estimate_reps_duration_seconds(target_reps)
                    rep_row["estimated_duration_seconds"] = estimated
                    active_seconds += estimated
                    set_duration_seconds += estimated
            else:
                rep_row["target_reps"] = None
                rep_row["target"] = None

            if target_duration_seconds is not None:
                duration_seconds = max(1, int(round(float(target_duration_seconds))))
                rep_row["target_duration_seconds"] = duration_seconds
                rep_row["duration_seconds"] = duration_seconds
                if rep_mode == "time":
                    active_seconds += duration_seconds
                    set_duration_seconds += duration_seconds
                    timed_intervals += 1
                    timed_intervals_seconds += duration_seconds
            else:
                rep_row["target_duration_seconds"] = None
                rep_row["duration_seconds"] = None

            reps_rows[rep_index - 1] = rep_row

        set_row["reps"] = reps_rows
        set_row["set_no"] = int(set_row.get("set_no", set_index + 1) or (set_index + 1))
        set_row["mode"] = str(
            set_row.get("mode")
            or (reps_rows[0].get("mode") if reps_rows else fallback_mode)
            or fallback_mode
        )

        rest_after = max(0, int(set_row.get("rest_seconds_after", 0) or 0))
        set_row["rest_seconds_after"] = rest_after
        if set_index < total_sets - 1:
            rest_between_sets_seconds += rest_after

        set_summaries.append(
            {
                "set_id": int(set_row["set_no"]),
                "set_no": int(set_row["set_no"]),
                "mode": str(set_row["mode"]),
                "rep_variations": len(reps_rows),
                "reps_count": set_target_reps if set_target_reps > 0 else len(reps_rows),
                "target_reps": set_target_reps if set_target_reps > 0 else None,
                "duration_seconds": set_duration_seconds if set_duration_seconds > 0 else None,
                "target_duration_seconds": set_duration_seconds if set_duration_seconds > 0 else None,
                "rest_seconds_after": rest_after,
            }
        )

        normalized_sets[set_index] = set_row

    planned_total_seconds = active_seconds + rest_between_sets_seconds
    return {
        "sets_payload": normalized_sets,
        "set_summaries": set_summaries,
        "total_sets": total_sets,
        "total_intervals": total_intervals,
        "total_reps": total_reps,
        "timed_intervals": timed_intervals,
        "timed_intervals_seconds": timed_intervals_seconds,
        "active_seconds": active_seconds,
        "rest_between_sets_seconds": rest_between_sets_seconds,
        "rest_seconds_after_exercise": int(normalized_sets[-1].get("rest_seconds_after", 0) or 0) if normalized_sets else 0,
        "planned_total_seconds": planned_total_seconds,
        "total_minutes": max(1, math.ceil(planned_total_seconds / 60)) if planned_total_seconds > 0 else 0,
    }
