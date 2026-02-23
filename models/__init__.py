from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase
from beanie import init_beanie
from .db import db, client
from .users import User , UserProfile
from .verification_code import VerificationCode
from .auth import OAuthAccount, AuthSession, EmailOTP 
from .devices import Device
from .content import Exercise, WorkoutTemplate, WorkoutProgram, MeditationItem
from .workouts import UserWorkout, WorkoutRun, ExerciseFeedbackEvent
from .meditation_run import MeditationRun
from .engagement import DevicePushToken , Reminder , PushDeliveryLog , AnalyticsEvent , OfflineDownloadRecord
from .progress import (
    ActivityEvent, WeeklyFocusWeek, AchievementDef, UserAchievement,
    UserExerciseStats, BodyMeasurement, BeforeAfterPhoto
)
from .subscription import SubscriptionPlan, Subscription, SubscriptionTransaction
from .promo import PromoCodeBatch, PromoCode, PromoRedemption
from .ai import (
    AiUsageMonthly, AiPlan, AiRequest,
    AiChatThread, AiChatMessage, RewardedGrant
)
from .notifications import Notification, ReminderSettings
from .admin import AdminUser, AdminAuditLog
from .password_reset import PasswordReset
from .social import SocialAccount
from .health import UserHealthIntegration, UserHealthStepDaily
ALL_MODELS = [
    User,
    VerificationCode,
    OAuthAccount, AuthSession, EmailOTP,
    Device,
    Exercise, WorkoutTemplate, WorkoutProgram, MeditationItem,
    UserWorkout, WorkoutRun, ExerciseFeedbackEvent,
    MeditationRun,
    ActivityEvent, WeeklyFocusWeek,
    AchievementDef, UserAchievement,
    UserExerciseStats,
    BodyMeasurement, BeforeAfterPhoto,
    DevicePushToken, Reminder, PushDeliveryLog, AnalyticsEvent , OfflineDownloadRecord,
    SubscriptionPlan, Subscription, SubscriptionTransaction,

    PromoCodeBatch, PromoCode, PromoRedemption,

    AiUsageMonthly, AiPlan, AiRequest,
    AiChatThread, AiChatMessage, RewardedGrant,

    Notification, ReminderSettings,

    AdminUser, AdminAuditLog,
    PasswordReset,
    SocialAccount,
    UserHealthIntegration, UserHealthStepDaily,
]



async def init_models(db: AsyncIOMotorDatabase) -> None:
    await init_beanie(database=db, document_models=ALL_MODELS)
