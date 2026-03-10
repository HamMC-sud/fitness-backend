from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase
from beanie import init_beanie
from .db import db, client
from .users import User, UserProfile
from .verification_code import VerificationCode
from .auth import OAuthAccount, AuthSession, EmailOTP
from .content import Exercise, MeditationItem
from .workouts import UserWorkout, WorkoutRun, ExerciseFeedbackEvent
from .meditation_run import MeditationRun
from .engagement import AnalyticsEvent
from .progress import (
    UserAchievement,
    BodyMeasurement,
)
from .subscription import SubscriptionPlan, Subscription, SubscriptionTransaction
from .promo import PromoCodeBatch, PromoCode, PromoRedemption
from .ai import (
    AiUsageMonthly, AiPlan, AiRequest,
    AiChatThread, AiChatMessage, AiDailyRecommendation, RewardedGrant
)
from .admin import AdminUser
from .social import SocialAccount
from .health import UserHealthStepDaily
from .content_library import ContentAsset

ALL_MODELS = [
    User,
    VerificationCode,
    OAuthAccount, AuthSession, EmailOTP,
    Exercise, MeditationItem,
    UserWorkout, WorkoutRun, ExerciseFeedbackEvent,
    MeditationRun,
    UserAchievement,
    BodyMeasurement,
    AnalyticsEvent,
    SubscriptionPlan, Subscription, SubscriptionTransaction,
    PromoCodeBatch, PromoCode, PromoRedemption,
    AiUsageMonthly, AiPlan, AiRequest,
    AiChatThread, AiChatMessage, AiDailyRecommendation, RewardedGrant,
    AdminUser,
    SocialAccount,
    UserHealthStepDaily,
    ContentAsset,
]



async def init_models(db: AsyncIOMotorDatabase) -> None:
    await init_beanie(database=db, document_models=ALL_MODELS)
