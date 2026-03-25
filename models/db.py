import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from urllib.parse import urlparse

load_dotenv()


def _db_name_from_uri(uri: str) -> str | None:
    """
    Extract database name from URI path: mongodb://host/<db>?...
    Returns None when URI has no database in path.
    """
    path = (urlparse(uri).path or "").strip("/")
    return path or None


mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/fitness_db")
db_name = (
    os.getenv("DB_NAME")
    or os.getenv("DATABASE_NAME")
    or _db_name_from_uri(mongo_uri)
    or "fitness_db"
)

client = AsyncIOMotorClient(mongo_uri)
db = client[db_name]
