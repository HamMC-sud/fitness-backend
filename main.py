import os
from contextlib import asynccontextmanager

from fastapi import FastAPI , WebSocket, WebSocketDisconnect
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

app.mount("/statics", StaticFiles(directory="statics"), name="statics")

app.include_router(api_router)

@app.get("/healthcheck", status_code=200)
async def healthcheck():
    return {"status": "ok"} 


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        dead_connections = []

        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                dead_connections.append(connection)

        for conn in dead_connections:
            self.disconnect(conn)


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    try:
        await manager.send_personal_message("Connected to WebSocket", websocket)

        while True:
            data = await websocket.receive_text()
            await manager.send_personal_message(f"You sent: {data}", websocket)
            await manager.broadcast(f"Broadcast: {data}")

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast("A client disconnected")
    except Exception:
        manager.disconnect(websocket)