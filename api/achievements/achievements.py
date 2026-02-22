from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from api.auth.config import get_current_user
from models import UserAchievement
from models.workouts import WorkoutRun
from schemas.achievements import (
    AchievementProgressListOut,
    AchievementProgressOut,
    AchievementCatalogItemOut,
    AchievementCatalogOut,
    AchievementCategoryOut,
    AchievementPushBatchIn,
    AchievementPushBatchOut,
    AchievementPushIn,
    AchievementPushOut,
    AchievementItemOut,
    AchievementsOut,
    I18nText,
    UserAchievementIn,
    UserAchievementOut,
    UserAchievementsListOut,
    UserAchievementPatchIn,
)


router = APIRouter(tags=["achievements"])
BASE_POINTS_PER_STEP = 50

ACHIEVEMENT_CATALOG = {
    "A": [
        {"id": "str_003", "name_ru": "Искра", "name_en": "Spark", "description_ru": "Стрик 3 дня подряд", "description_en": "3 Day workout streak", "logic": "streak >= 3"},
        {"id": "str_007", "name_ru": "В огне", "name_en": "On Fire", "description_ru": "Стрик 7 дней подряд", "description_en": "7 Day workout streak", "logic": "streak >= 7"},
        {"id": "str_014", "name_ru": "Две недели", "name_en": "Two Weeks Strong", "description_ru": "Стрик 14 дней подряд", "description_en": "14 Day workout streak", "logic": "streak >= 14"},
        {"id": "str_030", "name_ru": "Неудержимый", "name_en": "Unstoppable", "description_ru": "Стрик 30 дней подряд", "description_en": "30 Day workout streak", "logic": "streak >= 30"},
        {"id": "str_090", "name_ru": "Режим зверя", "name_en": "90 Day Grind", "description_ru": "Стрик 90 дней подряд", "description_en": "90 Day workout streak", "logic": "streak >= 90"},
        {"id": "str_365", "name_ru": "Воин года", "name_en": "Yearly Warrior", "description_ru": "Стрик 365 дней", "description_en": "365 Day streak", "logic": "streak >= 365"},
        {"id": "str_perf_mo", "name_ru": "Идеальный месяц", "name_en": "Perfect Month", "description_ru": "Тренировки каждый день месяца", "description_en": "Workout every day in a calendar month", "logic": "days_in_month == workouts_this_month"},
        {"id": "str_weekend", "name_ru": "Воин выходного дня", "name_en": "Weekend Warrior", "description_ru": "Тренировки в Сб и Вс 4 недели подряд", "description_en": "Workout Sat & Sun for 4 weeks in a row", "logic": "weekend_streak >= 4"},
        {"id": "str_early", "name_ru": "Жаворонок", "name_en": "Early Bird", "description_ru": "10 тренировок до 8 утра", "description_en": "Complete 10 workouts before 8 AM", "logic": "early_workouts_count >= 10"},
        {"id": "str_night", "name_ru": "Сова", "name_en": "Night Owl", "description_ru": "10 тренировок после 21:00", "description_en": "Complete 10 workouts after 9 PM", "logic": "night_workouts_count >= 10"},
    ],
    "B": [
        {"id": "mil_run_5k", "name_ru": "Первые 5 км", "name_en": "First 5k Run", "description_ru": "Пробежать 5 км за раз", "description_en": "Complete a 5km run session", "logic": "run_distance_km_session >= 5"},
        {"id": "mil_run_10k", "name_ru": "Бегун 10к", "name_en": "10k Runner", "description_ru": "Пробежать 10 км за раз", "description_en": "Complete a 10km run session", "logic": "run_distance_km_session >= 10"},
        {"id": "mil_run_21k", "name_ru": "Полумарафон", "name_en": "Half Marathon", "description_ru": "Пробежать 21 км за раз", "description_en": "Complete 21km in one session", "logic": "run_distance_km_session >= 21"},
        {"id": "mil_run_42k", "name_ru": "Марафонец", "name_en": "Marathon Man", "description_ru": "Пробежать 42 км за раз", "description_en": "Complete 42km in one session", "logic": "run_distance_km_session >= 42"},
        {"id": "mil_hike_100", "name_ru": "Турист 100", "name_en": "100km Hiked", "description_ru": "Пройти 100 км (всего)", "description_en": "Accumulate 100km total hiking", "logic": "hike_distance_km_total >= 100"},
        {"id": "mil_hike_500", "name_ru": "Турист 500", "name_en": "500km Hiked", "description_ru": "Пройти 500 км (всего)", "description_en": "Accumulate 500km total hiking", "logic": "hike_distance_km_total >= 500"},
        {"id": "mil_cal_1k", "name_ru": "1000 Калорий", "name_en": "1000 Calories", "description_ru": "Сжечь 1000 ккал (сумма)", "description_en": "Burn 1000 active calories total", "logic": "calories_total >= 1000"},
        {"id": "mil_cal_10k", "name_ru": "10,000 Калорий", "name_en": "10,000 Calories", "description_ru": "Сжечь 10,000 ккал (сумма)", "description_en": "Burn 10,000 active calories total", "logic": "calories_total >= 10000"},
        {"id": "mil_cal_100k", "name_ru": "Топка", "name_en": "Furnace", "description_ru": "Сжечь 100,000 ккал (сумма)", "description_en": "Burn 100,000 active calories total", "logic": "calories_total >= 100000"},
        {"id": "mil_vol_iron", "name_ru": "Железный человек", "name_en": "Iron Lifter", "description_ru": "Поднять 10 тонн (объем)", "description_en": "Lift 10,000 kg total volume", "logic": "lift_volume_kg_total >= 10000"},
        {"id": "mil_vol_tank", "name_ru": "Танк", "name_en": "The Tank", "description_ru": "Поднять 100 тонн (объем)", "description_en": "Lift 100,000 kg total volume", "logic": "lift_volume_kg_total >= 100000"},
        {"id": "mil_everest", "name_ru": "Эверест", "name_en": "Everest Height", "description_ru": "Набрать высоту 8848м", "description_en": "Climb equivalent of 8848m", "logic": "elevation_gain_m_total >= 8848"},
    ],
    "C": [
        {"id": "ch_pushup", "name_ru": "Мастер отжиманий", "name_en": "Push-Up Master", "description_ru": "Сделать 500 отжиманий (сумма)", "description_en": "Complete 500 Push-Ups (Total)", "logic": "pushups_total >= 500"},
        {"id": "ch_plank", "name_ru": "Профи планки", "name_en": "Plank Pro", "description_ru": "Простоять в планке 1 час (сумма)", "description_en": "Accumulate 60 minutes of Planking", "logic": "plank_seconds_total >= 3600"},
        {"id": "ch_squat", "name_ru": "Король приседа", "name_en": "Squat King", "description_ru": "Сделать 1000 приседаний (сумма)", "description_en": "Complete 1000 Squats (Total)", "logic": "squats_total >= 1000"},
        {"id": "ch_pullup", "name_ru": "Король турника", "name_en": "Pull-Up King", "description_ru": "Сделать 100 подтягиваний (сумма)", "description_en": "Complete 100 Pull-Ups (Total)", "logic": "pullups_total >= 100"},
        {"id": "ch_cardio", "name_ru": "Кардио-зверь", "name_en": "Cardio Beast", "description_ru": "24 часа кардио тренировок", "description_en": "Accumulate 24 hours of Cardio", "logic": "cardio_seconds_total >= 86400"},
        {"id": "ch_core", "name_ru": "Пресс-машина", "name_en": "Core Crusher", "description_ru": "50 тренировок на пресс", "description_en": "Complete 50 Ab workouts", "logic": "core_workouts_count >= 50"},
        {"id": "ch_legday", "name_ru": "Легенда дней ног", "name_en": "Leg Day Legend", "description_ru": "Не пропускал день ног 10 недель", "description_en": "Never skip leg day (10 weeks)", "logic": "legday_weeks_streak >= 10"},
        {"id": "ch_hiit", "name_ru": "HIIT Герой", "name_en": "HIIT Hero", "description_ru": "20 HIIT тренировок", "description_en": "Complete 20 HIIT sessions", "logic": "hiit_sessions_count >= 20"},
        {"id": "ch_yoga", "name_ru": "Йог", "name_en": "Daily Yogi", "description_ru": "30 занятий йогой", "description_en": "Complete 30 Yoga sessions", "logic": "yoga_sessions_count >= 30"},
        {"id": "ch_flex", "name_ru": "Гибкая сила", "name_en": "Flexible Flyer", "description_ru": "5 часов растяжки (сумма)", "description_en": "Stretch for 5 hours total", "logic": "stretching_seconds_total >= 18000"},
    ],
    "D": [
        {"id": "fun_tcode", "name_ru": "Ноги-макаронины", "name_en": "Legs Miserables", "description_ru": "Сделать > 100 приседаний за раз", "description_en": "Do a workout with > 100 squats", "logic": "squats_in_one_session > 100"},
        {"id": "fun_burpee", "name_ru": "Берпи и смех", "name_en": "Burpees & Belly Laughs", "description_ru": "Сделать 50 берпи за раз", "description_en": "Do 50 Burpees in one session", "logic": "burpees_in_one_session >= 50"},
        {"id": "fun_plank", "name_ru": "Бесконечная минута", "name_en": "Planks for Memories", "description_ru": "Планка 3+ минуты без перерыва", "description_en": "Hold a plank for 3+ minutes", "logic": "plank_set_seconds >= 180"},
        {"id": "fun_run", "name_ru": "Бегу за тако", "name_en": "Will Run For Tacos", "description_ru": "Сжечь 500 ккал на пробежке", "description_en": "Burn 500 calories in one run", "logic": "run_kcal_in_one_session >= 500"},
        {"id": "fun_night", "name_ru": "Режим зомби", "name_en": "Zombie Mode", "description_ru": "Тренировка с 2 до 5 утра", "description_en": "Workout between 2 AM and 5 AM", "logic": "workout_time in [02:00, 05:00]"},
        {"id": "fun_social", "name_ru": "Бро по залу", "name_en": "Gym Buddy", "description_ru": "Поделиться тренировкой 5 раз", "description_en": "Share a workout 5 times", "logic": "share_workout_count >= 5"},
    ],
    "E": [
        {"id": "eq_db_50", "name_ru": "Гантельный демон", "name_en": "Dumbbell Demon", "description_ru": "50 тренировок с гантелями", "description_en": "Complete 50 Dumbbell workouts", "logic": "dumbbell_workouts_count >= 50"},
        {"id": "eq_kb_50", "name_ru": "Король гирь", "name_en": "Kettlebell King", "description_ru": "50 тренировок с гирей", "description_en": "Complete 50 Kettlebell workouts", "logic": "kettlebell_workouts_count >= 50"},
        {"id": "eq_bw_100", "name_ru": "Бог калистеники", "name_en": "Calisthenics God", "description_ru": "100 тренировок с собств. весом", "description_en": "Complete 100 Bodyweight workouts", "logic": "bodyweight_workouts_count >= 100"},
        {"id": "eq_bar_50", "name_ru": "Босс штанги", "name_en": "Barbell Boss", "description_ru": "50 тренировок со штангой", "description_en": "Complete 50 Barbell workouts", "logic": "barbell_workouts_count >= 50"},
        {"id": "eq_bench", "name_ru": "Жим-Бро", "name_en": "Bench Press Bro", "description_ru": "100 подходов жима лежа", "description_en": "Complete 100 Sets of Bench Press", "logic": "bench_press_sets_total >= 100"},
    ],
    "F": [
        {"id": "time_10h", "name_ru": "Начало положено", "name_en": "Getting Started", "description_ru": "10 часов тренировок (всего)", "description_en": "10 Hours of total workout time", "logic": "total_workout_minutes >= 600"},
        {"id": "time_50h", "name_ru": "Вовлеченный", "name_en": "Committed", "description_ru": "50 часов тренировок (всего)", "description_en": "50 Hours of total workout time", "logic": "total_workout_minutes >= 3000"},
        {"id": "time_100h", "name_ru": "Посвященный", "name_en": "Dedicated", "description_ru": "100 часов тренировок (всего)", "description_en": "100 Hours of total workout time", "logic": "total_workout_minutes >= 6000"},
        {"id": "time_500h", "name_ru": "Одержимый", "name_en": "Obsessed", "description_ru": "500 часов тренировок (всего)", "description_en": "500 Hours of total workout time", "logic": "total_workout_minutes >= 30000"},
        {"id": "time_1k_h", "name_ru": "Мастерство", "name_en": "Mastery", "description_ru": "1000 часов тренировок (всего)", "description_en": "1000 Hours", "logic": "total_workout_minutes >= 60000"},
    ],
}

MAX_PROGRESS_BY_ID = {
    "str_003": 3, "str_007": 7, "str_014": 14, "str_030": 30, "str_090": 90, "str_365": 365,
    "str_perf_mo": 30, "str_weekend": 4, "str_early": 10, "str_night": 10,
    "mil_run_5k": 5, "mil_run_10k": 10, "mil_run_21k": 21, "mil_run_42k": 42,
    "mil_hike_100": 100, "mil_hike_500": 500, "mil_cal_1k": 1000, "mil_cal_10k": 10000,
    "mil_cal_100k": 100000, "mil_vol_iron": 10000, "mil_vol_tank": 100000, "mil_everest": 8848,
    "ch_pushup": 500, "ch_plank": 3600, "ch_squat": 1000, "ch_pullup": 100, "ch_cardio": 86400,
    "ch_core": 50, "ch_legday": 10, "ch_hiit": 20, "ch_yoga": 30, "ch_flex": 18000,
    "fun_tcode": 1, "fun_burpee": 50, "fun_plank": 180, "fun_run": 500, "fun_night": 1, "fun_social": 5,
    "eq_db_50": 50, "eq_kb_50": 50, "eq_bw_100": 100, "eq_bar_50": 50, "eq_bench": 100,
    "time_10h": 600, "time_50h": 3000, "time_100h": 6000, "time_500h": 30000, "time_1k_h": 60000,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_user_tz(user) -> ZoneInfo:
    tz_name = getattr(user, "timezone", None) or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def clamp_progress(current: float, target: float) -> float:
    if target <= 0:
        return 0.0
    return max(0.0, min(1.0, current / target))


def compute_streak(dates: set) -> int:
    if not dates:
        return 0
    last_day = max(dates)
    streak = 1
    while (last_day - timedelta(days=streak)) in dates:
        streak += 1
    return streak


def nth_completed_at(sorted_completed: List[datetime], n: int) -> Optional[datetime]:
    if n <= 0 or len(sorted_completed) < n:
        return None
    return sorted_completed[n - 1]


def milestone_item(
    key: str,
    category: str,
    title_ru: str,
    title_en: str,
    desc_ru: str,
    desc_en: str,
    unit: str,
    current: float,
    target: float,
    unlocked_at: Optional[datetime],
) -> AchievementItemOut:
    unlocked = current >= target
    return AchievementItemOut(
        key=key,
        category=category,
        title=I18nText(ru=title_ru, en=title_en),
        description=I18nText(ru=desc_ru, en=desc_en),
        unit=unit,
        current=current,
        target=target,
        progress=clamp_progress(current, target),
        unlocked=unlocked,
        unlocked_at=unlocked_at if unlocked else None,
    )


def _slug_code(name: str) -> str:
    s = "_".join((name or "").strip().lower().split())
    return "".join(ch for ch in s if ch.isalnum() or ch == "_")[:120] or "achievement"


def _to_user_achievement_out(x: UserAchievement) -> UserAchievementOut:
    return UserAchievementOut(
        id=str(x.id),
        achievement_code=x.achievement_code,
        name=getattr(x, "name", "achievement"),
        progress=float(x.progress or 0),
        max_progress=float(getattr(x, "max_progress", 100) or 100),
        points=int(getattr(x, "points", 0) or 0),
        unlocked_at=getattr(x, "unlocked_at", None),
    )


def _catalog_item_by_id(category: str, achievement_id: str) -> Optional[dict]:
    for it in ACHIEVEMENT_CATALOG.get(category, []):
        if it["id"] == achievement_id:
            return it
    return None


def _category_for_achievement(achievement_id: str) -> Optional[str]:
    for cat, items in ACHIEVEMENT_CATALOG.items():
        for it in items:
            if it["id"] == achievement_id:
                return cat
    return None


def _points_for(category: str, achievement_id: str) -> int:
    items = ACHIEVEMENT_CATALOG.get(category, [])
    for idx, it in enumerate(items):
        if it["id"] == achievement_id:
            return (idx + 1) * BASE_POINTS_PER_STEP
    return BASE_POINTS_PER_STEP


def _max_progress_for(achievement_id: str) -> float:
    return float(MAX_PROGRESS_BY_ID.get(achievement_id, 100))


def _clamp_to_max(progress: float, max_progress: float) -> float:
    return max(0.0, min(float(progress), float(max_progress)))


def _to_catalog_item_out(category: str, item: dict, doc: Optional[UserAchievement]) -> AchievementCatalogItemOut:
    max_progress = _max_progress_for(item["id"])
    progress = float(getattr(doc, "progress", 0) or 0) if doc else 0.0
    progress = _clamp_to_max(progress, max_progress)
    unlocked_at = getattr(doc, "unlocked_at", None) if doc else None
    unlocked = progress >= max_progress or unlocked_at is not None
    points_value = _points_for(category, item["id"])
    return AchievementCatalogItemOut(
        id=item["id"],
        category=category,
        name_ru=item["name_ru"],
        name_en=item["name_en"],
        description_ru=item["description_ru"],
        description_en=item["description_en"],
        logic=item["logic"],
        points=points_value,
        progress=progress,
        max_progress=max_progress,
        unlocked=unlocked,
        unlocked_at=unlocked_at,
    )


def _to_progress_out(achievement_id: str, doc: Optional[UserAchievement]) -> AchievementProgressOut:
    max_progress = _max_progress_for(achievement_id)
    progress = float(getattr(doc, "progress", 0) or 0) if doc else 0.0
    progress = _clamp_to_max(progress, max_progress)
    # Always compute points from catalog order in category (50/100/150...),
    # so GET /achievements/progress is stable even without DB rows.
    category_value = str(getattr(doc, "category", "") or "") if doc else ""
    if not category_value:
        category_value = _category_for_achievement(achievement_id) or ""
    points = _points_for(category_value, achievement_id) if category_value else BASE_POINTS_PER_STEP
    dt = getattr(doc, "unlocked_at", None) if doc else None
    return AchievementProgressOut(
        achievement_id=achievement_id,
        progress=progress,
        max_progress=max_progress,
        points=points,
        date=dt,
    )


@router.get("/achievements/progress", response_model=AchievementProgressListOut)
async def get_achievements_progress(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    docs = await UserAchievement.find(UserAchievement.user_id == current_user.id).to_list()
    by_code = {d.achievement_code: d for d in docs}

    items: List[AchievementProgressOut] = []
    for category in ACHIEVEMENT_CATALOG.values():
        for it in category:
            aid = it["id"]
            items.append(_to_progress_out(aid, by_code.get(aid)))

    return AchievementProgressListOut(items=items)


@router.get("/achievements/progress/{achievement_id}", response_model=AchievementProgressOut)
async def get_achievement_progress(achievement_id: str, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    found = False
    for category in ACHIEVEMENT_CATALOG.values():
        for it in category:
            if it["id"] == achievement_id:
                found = True
                break
        if found:
            break
    if not found:
        raise HTTPException(status_code=404, detail="Achievement not found")

    doc = await UserAchievement.find_one(
        UserAchievement.user_id == current_user.id,
        UserAchievement.achievement_code == achievement_id,
    )
    return _to_progress_out(achievement_id, doc)


@router.get("/achievements/catalog", response_model=AchievementCatalogOut)
async def get_achievements_catalog(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    docs = await UserAchievement.find(UserAchievement.user_id == current_user.id).to_list()
    by_code = {d.achievement_code: d for d in docs}

    categories: List[AchievementCategoryOut] = []
    for category, items in ACHIEVEMENT_CATALOG.items():
        categories.append(
            AchievementCategoryOut(
                category=category,
                points_per_achievement=BASE_POINTS_PER_STEP,
                items=[_to_catalog_item_out(category, it, by_code.get(it["id"])) for it in items],
            )
        )
    return AchievementCatalogOut(categories=categories, points_per_achievement=BASE_POINTS_PER_STEP)


@router.get("/achievements/catalog/{category}", response_model=AchievementCategoryOut)
async def get_achievements_category(category: str, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    cat = (category or "").strip().upper()
    if cat not in ACHIEVEMENT_CATALOG:
        raise HTTPException(status_code=404, detail="Category not found")

    docs = await UserAchievement.find(UserAchievement.user_id == current_user.id).to_list()
    by_code = {d.achievement_code: d for d in docs}

    return AchievementCategoryOut(
        category=cat,
        points_per_achievement=BASE_POINTS_PER_STEP,
        items=[_to_catalog_item_out(cat, it, by_code.get(it["id"])) for it in ACHIEVEMENT_CATALOG[cat]],
    )


@router.post("/achievements/catalog/{category}/push", response_model=AchievementPushOut)
async def push_achievement_progress(category: str, payload: AchievementPushIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    cat = (category or "").strip().upper()
    if cat not in ACHIEVEMENT_CATALOG:
        raise HTTPException(status_code=404, detail="Category not found")

    item = _catalog_item_by_id(cat, payload.achievement_id)
    if not item:
        raise HTTPException(status_code=404, detail="Achievement not found in this category")
    points_value = _points_for(cat, payload.achievement_id)
    max_progress = _max_progress_for(payload.achievement_id)
    progress_value = _clamp_to_max(payload.progress, max_progress)

    doc = await UserAchievement.find_one(
        UserAchievement.user_id == current_user.id,
        UserAchievement.achievement_code == payload.achievement_id,
    )

    if not doc:
        doc = UserAchievement(
            user_id=current_user.id,
            achievement_code=payload.achievement_id,
            category=cat,
            name=item["name_en"],
            logic=item["logic"],
            progress=progress_value,
            max_progress=max_progress,
            points=points_value,
            unlocked_at=utcnow() if progress_value >= max_progress else None,
        )
        await doc.insert()
    else:
        doc.category = cat
        doc.name = item["name_en"]
        doc.logic = item["logic"]
        doc.progress = progress_value
        doc.max_progress = max_progress
        doc.points = points_value
        if progress_value >= max_progress and doc.unlocked_at is None:
            doc.unlocked_at = utcnow()
        await doc.save()

    return AchievementPushOut(
        status="ok",
        achievement=_to_catalog_item_out(cat, item, doc),
    )


@router.post("/achievements/catalog/{category}/push-list", response_model=AchievementPushBatchOut)
async def push_achievement_progress_list(category: str, payload: AchievementPushBatchIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    cat = (category or "").strip().upper()
    if cat not in ACHIEVEMENT_CATALOG:
        raise HTTPException(status_code=404, detail="Category not found")

    results: List[AchievementCatalogItemOut] = []
    for it_in in payload.items:
        item = _catalog_item_by_id(cat, it_in.achievement_id)
        if not item:
            continue

        points_value = _points_for(cat, it_in.achievement_id)
        max_progress = _max_progress_for(it_in.achievement_id)
        progress_value = _clamp_to_max(it_in.progress, max_progress)

        doc = await UserAchievement.find_one(
            UserAchievement.user_id == current_user.id,
            UserAchievement.achievement_code == it_in.achievement_id,
        )

        if not doc:
            doc = UserAchievement(
                user_id=current_user.id,
                achievement_code=it_in.achievement_id,
                category=cat,
                name=item["name_en"],
                logic=item["logic"],
                progress=progress_value,
                max_progress=max_progress,
                points=points_value,
                unlocked_at=utcnow() if progress_value >= max_progress else None,
            )
            await doc.insert()
        else:
            doc.category = cat
            doc.name = item["name_en"]
            doc.logic = item["logic"]
            doc.progress = progress_value
            doc.max_progress = max_progress
            doc.points = points_value
            if progress_value >= max_progress and doc.unlocked_at is None:
                doc.unlocked_at = utcnow()
            await doc.save()

        results.append(_to_catalog_item_out(cat, item, doc))

    return AchievementPushBatchOut(status="ok", achievements=results)


@router.post("/achievements/item", response_model=UserAchievementOut)
async def upsert_user_achievement(payload: UserAchievementIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    code = (payload.achievement_code or "").strip() or _slug_code(payload.name)

    doc = await UserAchievement.find_one(
        UserAchievement.user_id == current_user.id,
        UserAchievement.achievement_code == code,
    )

    default_max = float(getattr(doc, "max_progress", 100) or 100) if doc else 100.0
    progress_value = _clamp_to_max(payload.progress, default_max)
    unlocked_at = utcnow() if progress_value >= default_max else None

    if doc:
        doc.name = payload.name
        doc.progress = progress_value
        doc.max_progress = default_max
        doc.points = payload.points
        if unlocked_at is not None:
            doc.unlocked_at = unlocked_at
        await doc.save()
        return _to_user_achievement_out(doc)

    doc = UserAchievement(
        user_id=current_user.id,
        achievement_code=code,
        category="custom",
        name=payload.name,
        logic=None,
        progress=progress_value,
        max_progress=default_max,
        points=payload.points,
        unlocked_at=unlocked_at,
    )
    await doc.insert()
    return _to_user_achievement_out(doc)


@router.get("/achievements/items", response_model=UserAchievementsListOut)
async def list_user_achievements(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    items = await UserAchievement.find(UserAchievement.user_id == current_user.id).sort("-updated_at").to_list()
    return UserAchievementsListOut(items=[_to_user_achievement_out(x) for x in items])


@router.get("/achievements/item/{achievement_code}", response_model=UserAchievementOut)
async def get_user_achievement(achievement_code: str, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    code = (achievement_code or "").strip()
    doc = await UserAchievement.find_one(
        UserAchievement.user_id == current_user.id,
        UserAchievement.achievement_code == code,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Achievement not found")

    return _to_user_achievement_out(doc)


@router.patch("/achievements/item/{achievement_code}", response_model=UserAchievementOut)
async def patch_user_achievement(achievement_code: str, payload: UserAchievementPatchIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    code = (achievement_code or "").strip()
    doc = await UserAchievement.find_one(
        UserAchievement.user_id == current_user.id,
        UserAchievement.achievement_code == code,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Achievement not found")

    if payload.name is not None:
        doc.name = payload.name
    if payload.progress is not None:
        max_progress = float(getattr(doc, "max_progress", 100) or 100)
        doc.progress = _clamp_to_max(payload.progress, max_progress)
        if doc.progress >= max_progress and doc.unlocked_at is None:
            doc.unlocked_at = utcnow()
    if payload.points is not None:
        doc.points = payload.points

    await doc.save()
    return _to_user_achievement_out(doc)


@router.get("/achievements", response_model=AchievementsOut)
async def get_achievements(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    tz = get_user_tz(current_user)

    runs = await WorkoutRun.find({
        "user_id": current_user.id,
        "completed_at": {"$ne": None},
    }).to_list()

    completed_ats: List[datetime] = []
    total_seconds = 0
    total_calories = 0.0
    days_set = set()

    for r in runs:
        ca = getattr(r, "completed_at", None)
        if ca:
            ca_utc = ensure_aware_utc(ca)
            completed_ats.append(ca_utc)
            days_set.add(ca_utc.astimezone(tz).date())

        ts = getattr(r, "total_seconds", None)
        if isinstance(ts, int) and ts >= 0:
            total_seconds += ts

        kcal = getattr(r, "calories_estimated", None)
        if isinstance(kcal, (int, float)) and kcal >= 0:
            total_calories += float(kcal)

    completed_ats.sort()
    workouts_total = len(completed_ats)
    streak_days = compute_streak(days_set)
    hours_total = total_seconds / 3600.0

    items: List[AchievementItemOut] = []

    workout_targets = [1, 10, 50, 100, 500]
    for t in workout_targets:
        ua = nth_completed_at(completed_ats, t)
        items.append(
            milestone_item(
                key=f"workouts_{t}",
                category="workouts",
                title_ru=f"{t} тренировк(а/и)",
                title_en=f"{t} workouts",
                desc_ru=f"Завершите {t} тренировк(а/и)",
                desc_en=f"Complete {t} workouts",
                unit="workouts",
                current=float(workouts_total),
                target=float(t),
                unlocked_at=ua,
            )
        )

    streak_targets = [7, 30, 100]
    last_completed_at = completed_ats[-1] if completed_ats else None
    for t in streak_targets:
        items.append(
            milestone_item(
                key=f"streak_{t}",
                category="streak",
                title_ru=f"Серия {t} дней",
                title_en=f"{t}-day streak",
                desc_ru=f"Тренируйтесь {t} дней подряд",
                desc_en=f"Train {t} days in a row",
                unit="days",
                current=float(streak_days),
                target=float(t),
                unlocked_at=last_completed_at,
            )
        )

    calorie_targets = [1000, 5000, 10000]
    for t in calorie_targets:
        items.append(
            milestone_item(
                key=f"calories_{t}",
                category="calories",
                title_ru=f"{t} ккал",
                title_en=f"{t} kcal",
                desc_ru=f"Сожгите {t} ккал",
                desc_en=f"Burn {t} kcal",
                unit="kcal",
                current=float(total_calories),
                target=float(t),
                unlocked_at=last_completed_at,
            )
        )

    hour_targets = [10, 50, 100]
    for t in hour_targets:
        items.append(
            milestone_item(
                key=f"time_{t}h",
                category="time",
                title_ru=f"{t} часов",
                title_en=f"{t} hours",
                desc_ru=f"Потренируйтесь суммарно {t} часов",
                desc_en=f"Train for {t} total hours",
                unit="hours",
                current=float(hours_total),
                target=float(t),
                unlocked_at=last_completed_at,
            )
        )

    return AchievementsOut(
        items=items,
        totals={
            "workouts_total": workouts_total,
            "streak_days": streak_days,
            "total_seconds": total_seconds,
            "total_hours": hours_total,
            "total_calories": total_calories,
        },
    )
