import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from beanie import init_beanie

from models import db, client, ALL_MODELS
from api.api_router import api_router
os.makedirs("static", exist_ok=True)
os.makedirs("static/uploads/profile", exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_beanie(
        database=db,
        document_models=ALL_MODELS,
    )
    yield
    client.close()

app = FastAPI(
    lifespan=lifespan,
    title="fitness_backend",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(api_router)

@app.get("/healthcheck", status_code=200)
async def healthcheck():
    return {"status": "ok"} 