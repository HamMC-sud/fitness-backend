import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/fitness_db")

client = AsyncIOMotorClient(MONGO_URI)

# If URI contains /fitness_db -> get_default_database() works
db = client.get_default_database() or client["fitness_db"]
