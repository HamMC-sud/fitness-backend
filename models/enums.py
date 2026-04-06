from __future__ import annotations
from enum import Enum


class Region(str, Enum):
    RU = "RU"
    INTL = "INTL"


class Language(str, Enum):
    ru = "ru"
    en = "en"


class UnitSystem(str, Enum):
    metric = "metric"
    imperial = "imperial"


class HealthProvider(str, Enum):
    apple_health = "apple_health"
    google_fit = "google_fit"


class Platform(str, Enum):
    ios = "ios"
    android = "android"


class PushProvider(str, Enum):
    fcm = "fcm"
    rustore = "rustore"

class Interest(str, Enum):
    home = "home"
    gym = "gym"

class Gender(str, Enum):
    male = "male"
    female = "female"
    prefer_not_to_say = "prefer_not_to_say"


class ActivityLevel(str, Enum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"


class Goal(str, Enum):
    lose_weight = "lose_weight"
    build_muscle = "build_muscle"
    get_fitter = "get_fitter"
    endurance = "endurance"
    flexibility = "flexibility"


class Preference(str, Enum):
    strength = "strength"
    meditation_yoga = "meditation_yoga"
    cardio = "cardio"
    stretching = "stretching"


class Equipment(str, Enum):
    home = "home"
    gym = "gym"

    @classmethod
    def normalize(cls, value: object) -> "Equipment":
        if isinstance(value, cls):
            return value

        raw = str(value or "").strip().lower()
        token = raw.replace("-", "_")
        token = token.replace("&", "and")
        token = token.replace(" ", "_")

        home_tokens = {
            "home",
            "no equipment",
            "no_equipment",
            "bodyweight",
            "bands",
            "resistance bands",
            "resistance_bands",
        }
        gym_tokens = {
            "gym",
            "dumbbells",
            "pull-up bar",
            "pull_up_bar",
            "pullup bar",
            "pullup_bar",
            "barbell & bench",
            "barbell_and_bench",
            "barbell_&_bench",
            "barbell_bench",
            "barbell",
            "machine",
            "cable",
        }

        if raw in home_tokens or token in home_tokens:
            return cls.home
        if raw in gym_tokens or token in gym_tokens:
            return cls.gym

        raise ValueError(f"Unsupported equipment: {value}")

    @classmethod
    def normalize_many(cls, value: object) -> list["Equipment"]:
        if value is None:
            return []

        items = value if isinstance(value, (list, tuple, set)) else [value]
        out: list[Equipment] = []
        for item in items:
            normalized = cls.normalize(item)
            if normalized not in out:
                out.append(normalized)
        return out

class Injury(str, Enum):
    none = "none"
    back_pain = "back_pain"
    knee_issues = "knee_issues"
    shoulder_issues = "shoulder_issues"
    no_jumping = "no_jumping"


class ExerciseMode(str, Enum):
    reps = "reps"
    time = "time"


class Difficulty(str, Enum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"

    @classmethod
    def normalize(cls, value: object) -> "Difficulty":
        if isinstance(value, cls):
            return value

        raw = str(value or "").strip().lower()
        token = raw.replace("-", "_").replace(" ", "_")

        aliases = {
            "easy": "beginner",
            "novice": "beginner",
            "starter": "beginner",
            "medium": "intermediate",
            "normal": "intermediate",
            "all_levels": "intermediate",
            "all_level": "intermediate",
            "any": "intermediate",
        }
        token = aliases.get(token, token)
        try:
            return cls(token)
        except Exception:
            raise ValueError(f"Unsupported difficulty: {value}")


class WorkoutType(str, Enum):
    strength = "strength"
    cardio = "cardio"
    hiit = "hiit"
    stretching = "stretching"
    yoga = "yoga"

    @classmethod
    def normalize(cls, value: object) -> "WorkoutType":
        if isinstance(value, cls):
            return value

        raw = str(value or "").strip().lower()
        if not raw:
            raise ValueError(f"Unsupported workout type: {value}")

        token = (
            raw.replace("-", "_")
            .replace(" ", "_")
            .replace("&", "_")
            .replace("/", "_")
        )
        token = "_".join(part for part in token.split("_") if part)

        aliases = {
            "strength_training": "strength",
            "power": "strength",
            "aerobic": "cardio",
            "flexibility": "stretching",
        }
        token = aliases.get(token, token)
        try:
            return cls(token)
        except Exception:
            raise ValueError(f"Unsupported workout type: {value}")

    @classmethod
    def expand(cls, value: object) -> list["WorkoutType"]:
        if isinstance(value, cls):
            return [value]

        raw = str(value or "").strip().lower()
        if not raw:
            return []

        parts_by_comma = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts_by_comma) > 1:
            out: list[WorkoutType] = []
            for p in parts_by_comma:
                for wt in cls.expand(p):
                    if wt not in out:
                        out.append(wt)
            return out

        token = (
            raw.replace("-", "_")
            .replace(" ", "_")
            .replace("&", "_")
            .replace("/", "_")
        )
        token = "_".join(part for part in token.split("_") if part)

        # Legacy combined values like "strength_cardio".
        if "_" in token:
            chunks = [c for c in token.split("_") if c]
            if chunks and all(c in {x.value for x in cls} for c in chunks):
                out: list[WorkoutType] = []
                for c in chunks:
                    wt = cls(c)
                    if wt not in out:
                        out.append(wt)
                return out

        return [cls.normalize(token)]

    @classmethod
    def normalize_many(cls, value: object) -> list["WorkoutType"]:
        if value is None:
            return []

        items = value if isinstance(value, (list, tuple, set)) else [value]
        out: list[WorkoutType] = []
        for item in items:
            for wt in cls.expand(item):
                if wt not in out:
                    out.append(wt)
        return out


class Feedback(str, Enum):
    easy = "easy"
    normal = "normal"
    hard = "hard"


class SubscriptionStatus(str, Enum):
    active = "active"
    expired = "expired"
    canceled = "canceled"
    grace = "grace"


class SubscriptionSource(str, Enum):
    appstore = "appstore"
    googleplay = "googleplay"
    rustore = "rustore"
    web = "web"
    promo = "promo"


class PromoStatus(str, Enum):
    active = "active"
    disabled = "disabled"


class AiRequestType(str, Enum):
    generate_plan = "generate_plan"
    reroll = "reroll"
    adjust = "adjust"
    chat = "chat"


class AiRequestStatus(str, Enum):
    ok = "ok"
    error = "error"


class NotificationType(str, Enum):
    marketing = "marketing"
    subscription = "subscription"
    ai = "ai"
    system = "system"


class MediaType(str, Enum):
    workout = "workout"
    yoga = "yoga"
    meditation = "meditation"


class PhotoSlot(str, Enum):
    front = "front"
    side = "side"
    back = "back"
    other = "other"


    
class MuscleGroup(str, Enum):
    chest = "chest"
    back = "back"
    shoulders = "shoulders"
    biceps = "biceps"
    triceps = "triceps"
    core = "core"
    quads = "quads"
    glutes = "glutes"
    hamstrings = "hamstrings"
    calves = "calves"
    full_body = "full_body"
    cardio = "cardio"
