"""FastAPI application: WebSocket live stream, REST control/ingest endpoints, static dashboard."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .engine import HydroAuraEngine

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
engine = HydroAuraEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.start()
    yield
    await engine.stop()


app = FastAPI(title="HydroAura 4D Pipeline", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """The dashboard is served in development: always revalidate so edits show up on reload."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.websocket("/ws/stream")
async def ws_stream(ws: WebSocket):
    """Live 4D stream: a ``hello`` snapshot, then ``tick`` messages (telemetry, fused products, events)."""
    await ws.accept()
    await ws.send_text(json.dumps(engine.hello(), separators=(",", ":")))
    engine.hub.clients.add(ws)
    try:
        while True:
            await ws.receive_text()      # clients may send pings/commands; we only need disconnect detection
    except WebSocketDisconnect:
        pass
    finally:
        engine.hub.clients.discard(ws)


@app.get("/api/health")
def health():
    return {"status": "ok", "clients": len(engine.hub.clients), "paused": engine.paused,
            "ingestion": engine.ingest.stats(), "planner": engine.planner.status()}


@app.get("/api/state")
def state():
    return engine.hello()


@app.post("/api/reset")
def reset():
    engine.reset()
    return {"ok": True}


@app.post("/api/pause")
def pause(paused: bool = True):
    engine.paused = paused
    return {"paused": engine.paused}


@app.post("/api/speed/{scale}")
def speed(scale: float):
    if not 0.25 <= scale <= 16:
        raise HTTPException(422, "scale must be within 0.25..16")
    # SurveyConfig is frozen; the engine reads time_scale each tick through the scenario, so swap it
    from dataclasses import replace
    engine.scenario = replace(engine.scenario, survey=replace(engine.scenario.survey, time_scale=scale))
    engine.sim.cfg = engine.scenario.survey          # (only pacing reads time_scale)
    return {"time_scale": scale}


@app.post("/api/ingest")
async def ingest(request: Request):
    """External sensor gateway: POST one frame or a list of frames (telemetry / mbes / sbp)."""
    body = await request.json()
    frames = body if isinstance(body, list) else [body]
    accepted = sum(1 for f in frames if engine.ingest.ingest(f) is not None)
    return JSONResponse({"accepted": accepted, "rejected": len(frames) - accepted})


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
