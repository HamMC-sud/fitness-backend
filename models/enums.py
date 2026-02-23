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

class Location(str, Enum):
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
    bodyweight = "bodyweight"
    dumbbells = "dumbbells"
    pullup_bar = "pullup_bar"
    resistance_bands = "resistance_bands"
    barbell_bench = "barbell_bench"


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


class WorkoutType(str, Enum):
    strength = "strength"
    cardio = "cardio"
    hiit = "hiit"
    stretching = "stretching"
    yoga = "yoga"


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
