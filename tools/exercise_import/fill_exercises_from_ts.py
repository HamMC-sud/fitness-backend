from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pymongo import MongoClient


BASE_URL = os.getenv("EXERCISE_MEDIA_BASE_URL", "http://26.214.57.127:8000").rstrip("/")
MEDIA_POOL = [f"ex_{i:03d}" for i in range(1, 15)]


def parse_time_to_defaults(time_str: str) -> dict[str, Any]:
    s = (time_str or "").lower().strip()

    m = re.search(r"(\d+)\s*reps?", s)
    if m:
        reps = max(1, min(500, int(m.group(1))))
        return {"mode": "reps", "reps": reps, "duration_seconds": None, "media_duration": 40, "sets": 4, "rest_seconds_after": 60}

    m = re.search(r"(\d+)\s*sec", s)
    if m:
        dur = max(5, min(3600, int(m.group(1))))
        return {"mode": "time", "reps": None, "duration_seconds": dur, "media_duration": dur, "sets": 4, "rest_seconds_after": 60}

    m = re.search(r"(\d+)\s*min", s)
    if m:
        dur = max(5, min(3600, int(m.group(1)) * 60))
        return {"mode": "time", "reps": None, "duration_seconds": dur, "media_duration": dur, "sets": 4, "rest_seconds_after": 60}

    return {"mode": "reps", "reps": 12, "duration_seconds": None, "media_duration": 40, "sets": 4, "rest_seconds_after": 60}


def build_set_plan(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    sets = int(parsed.get("sets") or 4)
    rest = int(parsed.get("rest_seconds_after") or 60)
    mode = str(parsed.get("mode") or "reps")
    reps = parsed.get("reps")
    seconds = parsed.get("duration_seconds")

    out: list[dict[str, Any]] = []
    for i in range(1, sets + 1):
        item: dict[str, Any] = {"set_no": i, "rest_seconds_after": rest}
        if mode == "reps":
            item["target_reps"] = int(reps or 12)
            item["target_duration_seconds"] = None
        else:
            item["target_reps"] = None
            item["target_duration_seconds"] = int(seconds or parsed.get("media_duration") or 30)
        out.append(item)
    return out


def build_sets_reps(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    sets = int(parsed.get("sets") or 4)
    rest = int(parsed.get("rest_seconds_after") or 60)
    mode = str(parsed.get("mode") or "reps")
    reps = parsed.get("reps")
    seconds = parsed.get("duration_seconds")
    fallback_seconds = int(seconds or parsed.get("media_duration") or 30)

    out: list[dict[str, Any]] = []
    for i in range(1, sets + 1):
        rep_item = {
            "rep_no": 1,
            "target_reps": int(reps) if (mode == "reps" and reps is not None) else None,
            "target_duration_seconds": fallback_seconds if mode == "time" else None,
        }
        out.append(
            {
                "set_no": i,
                "rest_seconds_after": rest,
                "reps": [rep_item],
            }
        )
    return out


def load_all_exercises(ts_path: Path) -> list[dict[str, Any]]:
    raw = ts_path.read_text(encoding="utf-8")
    start = raw.find("[")
    end = raw.rfind("];")
    if start < 0 or end < 0:
        raise RuntimeError("Could not locate allExercises array in TS file.")

    payload = raw[start : end + 1]
    return json.loads(payload)


def map_difficulty(level: str) -> str:
    v = (level or "").strip().lower()
    if v == "advanced":
        return "advanced"
    if v == "intermediate":
        return "intermediate"
    return "beginner"


def map_equipment(equipment: str) -> list[str]:
    v = (equipment or "").strip().lower()
    if "gym" in v:
        return ["gym"]
    return ["home"]


def map_workout_types(category: str) -> list[str]:
    v = (category or "").strip().lower()
    if "hiit" in v:
        return ["hiit", "cardio"]
    if "cardio" in v:
        return ["cardio"]
    if "stretch" in v:
        return ["stretching"]
    if "yoga" in v:
        return ["yoga", "stretching"]
    if "meditation" in v:
        return ["yoga"]
    return ["strength"]


def estimate_calories_per_minute(workout_types: list[str], level: str) -> float:
    type_rates = {
        "strength": 6.5,
        "cardio": 9.0,
        "hiit": 11.0,
        "stretching": 3.0,
        "yoga": 3.5,
    }
    base = max(type_rates.get(t, 6.0) for t in (workout_types or ["strength"]))
    lvl = (level or "").strip().lower()
    level_mult = 1.2 if lvl == "advanced" else (1.1 if lvl == "intermediate" else 1.0)
    return round(max(2.0, min(20.0, base * level_mult)), 1)


def normalize_instructions(instructions: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(instructions, list):
        return out

    for idx, row in enumerate(instructions, start=1):
        if not isinstance(row, dict):
            continue
        step = int(row.get("step") or idx)
        title_en = str(row.get("titleEn") or row.get("title") or "").strip()
        title_ru = str(row.get("titleRu") or row.get("title") or title_en).strip()
        desc_en = str(row.get("descriptionEn") or row.get("description") or "").strip()
        desc_ru = str(row.get("descriptionRu") or row.get("description") or desc_en).strip()
        out.append(
            {
                "step": max(1, step),
                "title": {"ru": title_ru, "en": title_en},
                "description": {"ru": desc_ru, "en": desc_en},
            }
        )
    return out


def normalize_common_mistakes(common_mistakes: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(common_mistakes, list):
        return out

    for row in common_mistakes:
        if not isinstance(row, dict):
            continue
        title_en = str(row.get("titleEn") or row.get("title") or "").strip()
        title_ru = str(row.get("titleRu") or row.get("title") or title_en).strip()
        desc_en = str(row.get("descriptionEn") or row.get("description") or "").strip()
        desc_ru = str(row.get("descriptionRu") or row.get("description") or desc_en).strip()
        out.append(
            {
                "title": {"ru": title_ru, "en": title_en},
                "description": {"ru": desc_ru, "en": desc_en},
            }
        )
    return out


def normalize_ai_text(ex: dict[str, Any], base_key: str) -> dict[str, str]:
    en_key = f"{base_key}En"
    ru_key = f"{base_key}Ru"
    base_val = str(ex.get(base_key) or "").strip()
    en_val = str(ex.get(en_key) or base_val).strip()
    ru_val = str(ex.get(ru_key) or base_val).strip()
    if not en_val:
        en_val = ru_val
    if not ru_val:
        ru_val = en_val
    return {"ru": ru_val, "en": en_val}


def main() -> None:
    load_dotenv()

    repo_root = Path(__file__).resolve().parents[2]
    ts_path = repo_root / "Flutter_Specs" / "exercises.ts"
    all_exercises = load_all_exercises(ts_path)

    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/fitness_db")
    client = MongoClient(mongo_uri)
    default_db = client.get_default_database()
    db = default_db if default_db is not None else client["fitness_db"]
    coll = db["exercises"]

    updated = 0
    missed = 0

    for idx, ex in enumerate(all_exercises):
        code = str(ex.get("id") or "").strip()
        if not code:
            continue

        media_code = MEDIA_POOL[idx % len(MEDIA_POOL)]
        parsed = parse_time_to_defaults(str(ex.get("time") or ""))

        name_en = str(ex.get("name") or code).strip()
        name_ru = str(ex.get("nameRu") or ex.get("name") or code).strip()
        workout_types = map_workout_types(str(ex.get("category") or ""))
        level_value = map_difficulty(str(ex.get("level") or ""))

        patch = {
            "code": code,
            "name.en": [str(ex.get("name") or code).strip()],
            "name.ru": [str(ex.get("nameRu") or ex.get("name") or code).strip()],
            "description.en": [name_en],
            "description.ru": [name_ru],
            "media.thumbnail_url": f"{BASE_URL}/upload_exercises/{media_code}/thumbnail.jpg",
            "media.video_url": f"{BASE_URL}/upload_exercises/{media_code}/video.mp4",
            "media.duration_seconds": parsed["media_duration"],
            "media.mode": parsed["mode"],
            "mode": parsed["mode"],
            "defaults.sets": int(parsed["sets"]),
            "defaults.reps": parsed["reps"],
            "defaults.duration_seconds": parsed["duration_seconds"],
            "defaults.rest_seconds_after": int(parsed["rest_seconds_after"]),
            "defaults.sets_reps": build_sets_reps(parsed),
            "defaults.set_plan": build_set_plan(parsed),
            "workout_type": workout_types,
            "equipment": map_equipment(str(ex.get("equipment") or "")),
            "contraindications": [],
            "difficulty": level_value,
            "instructions": normalize_instructions(ex.get("instructions")),
            "common_mistakes": normalize_common_mistakes(ex.get("commonMistakes")),
            "ai_technique": normalize_ai_text(ex, "aiTechnique"),
            "ai_mistakes": normalize_ai_text(ex, "aiMistakes"),
            "muscle_groups": [str(m).strip().lower() for m in (ex.get("muscles") or []) if str(m).strip()],
            "movement_type": str(ex.get("category") or "").strip().lower().replace(" ", "_"),
            "calories_per_minute": estimate_calories_per_minute(workout_types, level_value),
            "status": "active",
        }

        result = coll.update_one({"code": code}, {"$set": patch}, upsert=True)
        if result.matched_count or result.upserted_id is not None:
            updated += 1
        else:
            missed += 1

    print(json.dumps({"updated": updated, "missed": missed, "total_source": len(all_exercises)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
