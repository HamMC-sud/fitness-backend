from fastapi                        import APIRouter
from .auth.register                 import router as register_router
from .auth.auth                     import router as login_router
from .profile.profile               import router as profile_router
from .workout.workout               import router as workout_router
from .exercises.exercises           import router as exercises_router
from .program.program               import router as program_router
from .weekly_focus.weekly_focus     import router as weekly_focus_router
from .achievements.achievements     import router as achievements_router
from .meditations.meditations       import router as meditations_router
from .ai.ai                         import router as ai_router
from .subscription.subscription     import router as subscription_router
from .auth.social                   import router as social_router
from .engagement.engagement         import router as engagement_router
from .measurements.measurements     import router as measurements_router
from .health.health                 import router as health_router


api_router = APIRouter(prefix="/api/v1")

api_router.include_router(register_router)
api_router.include_router(login_router)
api_router.include_router(profile_router)
api_router.include_router(workout_router)
api_router.include_router(exercises_router)
api_router.include_router(program_router)
api_router.include_router(weekly_focus_router)
api_router.include_router(achievements_router)
api_router.include_router(meditations_router)
api_router.include_router(ai_router)
api_router.include_router(subscription_router)
api_router.include_router(social_router)
api_router.include_router(engagement_router)
api_router.include_router(measurements_router)
api_router.include_router(health_router)
