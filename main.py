import os
import logging
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI , Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from beanie import init_beanie

from models import db, client, ALL_MODELS
from api.api_router import api_router
from utils.api_i18n import augment_payload, localize_detail

logger = logging.getLogger(__name__)

# Keep startup directories aligned with mounted static path (/statics -> statics/*).
os.makedirs("statics", exist_ok=True)
os.makedirs("upload_exercises", exist_ok=True)


def _log_static_mount_status() -> None:
    statics_dir = Path("statics")
    uploads_dir = Path("upload_exercises")
    mp4_count = sum(1 for _ in uploads_dir.rglob("*.mp4")) if uploads_dir.exists() else 0
    logger.info(
        "Static mounts ready: /statics -> %s exists=%s; /upload_exercises -> %s exists=%s; mp4_files=%s",
        statics_dir.resolve(),
        statics_dir.exists(),
        uploads_dir.resolve(),
        uploads_dir.exists(),
        mp4_count,
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_beanie(
        database=db,
        document_models=ALL_MODELS,
    )
    _log_static_mount_status()
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
app.mount("/upload_exercises", StaticFiles(directory="upload_exercises"), name="upload_exercises")


def _should_skip_i18n(path: str) -> bool:
    return path.startswith("/docs") or path.startswith("/redoc") or path.startswith("/openapi")


async def _rebuild_json_response(response: Response, request: Request) -> Response:
    if _should_skip_i18n(request.url.path):
        return response

    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        return response

    body = getattr(response, "body", None)
    if body is None:
        body = b""
        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is None:
            return response
        async for chunk in body_iterator:
            body += chunk

    if not body:
        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
            background=response.background,
        )

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
            background=response.background,
        )

    localized = augment_payload(payload, response.status_code)
    headers = dict(response.headers)
    headers.pop("content-length", None)

    return Response(
        content=json.dumps(localized, ensure_ascii=False),
        status_code=response.status_code,
        headers=headers,
        media_type="application/json",
        background=response.background,
    )


@app.middleware("http")
async def add_bilingual_responses(request: Request, call_next):
    response = await call_next(request)
    return await _rebuild_json_response(response, request)


@app.exception_handler(StarletteHTTPException)
async def localized_http_exception_handler(request: Request, exc: StarletteHTTPException):
    response = await http_exception_handler(request, exc)
    return await _rebuild_json_response(response, request)


@app.exception_handler(RequestValidationError)
async def localized_validation_exception_handler(request: Request, exc: RequestValidationError):
    payload = augment_payload({"detail": exc.errors(), "detail_i18n": localize_detail(exc.errors())}, 422)
    return JSONResponse(status_code=422, content=payload)


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
