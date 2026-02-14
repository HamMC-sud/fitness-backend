from beanie.odm.fields import PydanticObjectId
from models import User

class AuthServices:
    async def find_user(self, user_id: str):
        try:
            oid = PydanticObjectId(user_id)
        except Exception:
            return None
        return await User.get(oid)
