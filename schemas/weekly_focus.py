from __future__ import annotations
from typing import  List
from pydantic import BaseModel

class DayPointsOut(BaseModel):
    date: str
    points: int
    workouts: int


class ActivityBreakdownOut(BaseModel):
    count: int
    points: int


class WeeklyFocusBreakdownOut(BaseModel):
    workouts: ActivityBreakdownOut
    yoga: ActivityBreakdownOut
    meditation: ActivityBreakdownOut


class WeeklyFocusOut(BaseModel):
    week_start_utc: str
    week_end_utc: str
    timezone: str
    goal_points: int
    total_points: int
    remaining_points: int
    progress: float
    breakdown: WeeklyFocusBreakdownOut
    days: List[DayPointsOut]

