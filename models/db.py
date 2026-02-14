import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

host = os.getenv("DB_HOST", "127.0.0.1")
port = os.getenv("DB_PORT", "27017")
name = os.getenv("DB_NAME", "fitness_db")

client = AsyncIOMotorClient(f"mongodb://{host}:{port}")
db = client[name]
