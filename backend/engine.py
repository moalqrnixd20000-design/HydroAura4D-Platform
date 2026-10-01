"""Orchestrator: simulator -> ingestion -> fusion -> planner -> broadcast, closed-loop.

``HydroAuraEngine.tick()`` is synchronous and deterministic (used by tests and headless runs);
``run()`` paces it in real time and pushes JSON messages to every WebSocket client.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from shared.config import Scenario
from shared.geo import local_to_geo

from .adaptive_planner import AdaptivePlanner, PlannerEvent
from .fusion_core import FusionConfig, FusionCore
from .ingestion import IngestionHub
from .mosaic import SeabedMosaic
from .simulator import SurveySimulator

log = logging.getLogger("hydroaura.engine")


@dataclass
class Hub:
    """Fan-out of pre-serialised JSON messages to WebSocket clients."""
    clients: set = field(default_factory=set)

    async def broadcast(self, msg: dict) -> None:
        if not self.clients:
            return
        text = json.dumps(msg, separators=(",", ":"))
        dead = []
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_text(text), timeout=1.0)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


class HydroAuraEngine:
    def __init__(self, scenario: Scenario | None = None, fusion_cfg: FusionConfig | None = None):
        self.scenario = scenario or Scenario()
        self.fusion_cfg = fusion_cfg
        self.hub = Hub()
        self.paused = False
        self._task: Optional[asyncio.Task] = None
        self.reset()

    # ---- lifecycle -----------------------------------------------------------------------
    def reset(self) -> None:
        self.sim = SurveySimulator(self.scenario)
        self.ingest = IngestionHub()
        self.fusion = FusionCore(self.fusion_cfg)
        self.planner = AdaptivePlanner(self.scenario.survey)
        self.mosaic = SeabedMosaic()
        self.ingest.subscribe(self.fusion.ingest)
        self.events: deque[dict] = deque(maxlen=300)
        self.track: list[list[float]] = []          # [east, north, t] history for late-joining clients
        self.anomaly_version = -1
        self.tick_no = 0
        self._log("info", "system", "Mission started - BHV_StandardSurvey active "
                  f"({self.planner.total_survey_wps}-waypoint lawnmower)")

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ---- one closed-loop step --------------------------------------------------------------
    def tick(self) -> dict:
        tel, mbes, sbp = self.sim.step(self.planner.current_target())
        for frame in (tel, mbes, sbp):
            self.ingest.ingest(frame)
        out = self.fusion.process()

        new_events: list[dict] = []
        pos = (self.sim.vehicle.x, self.sim.vehicle.y)

        for ev in out.events:
            a = ev.anomaly
            if ev.kind == "detected":
                new_events.append(self._log(
                    "info", "detection", f"{a.id} candidate {self._label(a.type)} at {a.lat:.5f}, {a.lon:.5f} "
                    f"(depth {a.depth_m:.0f} m, conf {a.confidence:.0%})", a.id))
            else:
                new_events.append(self._log(
                    "alert", "detection", f"HIGH-CONFIDENCE {self._label(a.type).upper()} {a.id}: severity "
                    f"{a.severity:.0f}/100, conf {a.confidence:.0%}, {a.lat:.5f}, {a.lon:.5f}, depth {a.depth_m:.0f} m", a.id))
                for pe in self.planner.handle_alert(a, *pos):
                    new_events.append(self._planner_log(pe))

        for pe in self.planner.update(*pos):
            new_events.append(self._planner_log(pe))
            if pe.kind == "mapping_complete" and pe.done_anomaly:
                self.fusion.tracker.mark_surveyed(pe.done_anomaly)

        if out.frames and "swath" in out.frames[-1].payload:
            sw = out.frames[-1].payload["swath"]
            self.mosaic.add(sw["east_m"], sw["north_m"], sw["depth_m"], sw["backscatter_db"])

        status = self.planner.status()
        phase = round(max(status["map_progress"], 0.01), 3) if status["mode"] == "ADAPTIVE_MAPPING" else 0.0
        route_changed = self.planner.version != getattr(self, "_route_ver", -1)
        self._route_ver = self.planner.version
        self.tick_no += 1
        if self.tick_no % 2 == 0:
            self.track.append([round(pos[0], 1), round(pos[1], 1), round(tel.t, 2), phase])

        msg = {
            "type": "tick", "t": tel.t, "sim_s": round(self.sim.sim_t, 2),
            "telemetry": tel.model_dump(exclude={"kind"}),
            "fused": out.frames[-1].payload if out.frames else {},
            "planner": status, "phase": phase,
            "events": new_events,
        }
        if self.fusion.tracker.version != self.anomaly_version:
            self.anomaly_version = self.fusion.tracker.version
            msg["anomalies"] = [a.model_dump() for a in self.fusion.tracker.snapshot()]
        if route_changed:
            msg["route"] = self.planner.route_geo()
        return msg

    # ---- async runner ----------------------------------------------------------------------
    async def run(self) -> None:
        nxt = time.monotonic()
        while True:
            if not self.paused:
                msg = self.tick()
                await self.hub.broadcast(msg)
            c = self.scenario.survey
            nxt += c.tick_dt_s / c.time_scale
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))
            if time.monotonic() - nxt > 1.0:        # fell behind (e.g. laptop sleep) - don't burst
                nxt = time.monotonic()

    # ---- snapshot for late joiners -----------------------------------------------------------
    def hello(self) -> dict:
        c = self.scenario.survey
        sw = local_to_geo(0, 0)
        ne = local_to_geo(c.width_m, c.height_m)
        return {
            "type": "hello",
            "survey": {"width_m": c.width_m, "height_m": c.height_m, "sw": [float(sw[0]), float(sw[1])],
                       "ne": [float(ne[0]), float(ne[1])], "time_scale": c.time_scale},
            "truth": self.sim.truth(),
            "route": self.planner.route_geo(),
            "anomalies": [a.model_dump() for a in self.fusion.tracker.snapshot()],
            "events": list(self.events),
            "track": list(self.track),
            "mosaic": self.mosaic.encode(),
            "planner": self.planner.status(),
            "paused": self.paused,
        }

    # ---- helpers -----------------------------------------------------------------------------
    @staticmethod
    def _label(t: str) -> str:
        return {"gas_plume": "water-column gas plume", "shallow_gas": "shallow gas pocket / hydrocarbon trap"}[t]

    def _log(self, level: str, cat: str, msg: str, anomaly_id: str | None = None) -> dict:
        ev = {"t": self.sim.t0 + self.sim.sim_t, "level": level, "category": cat, "message": msg,
              "anomaly_id": anomaly_id}
        self.events.append(ev)
        return ev

    def _planner_log(self, pe: PlannerEvent) -> dict:
        return self._log(pe.level, "routing", pe.message, pe.anomaly_id)
