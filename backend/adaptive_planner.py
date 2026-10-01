"""Adaptive survey planner - a lightweight analogue of the MOOS-IvP Helm.

MOOS-IvP (MIT, https://oceanai.mit.edu/moos-ivp/) steers a vehicle with a *behavior-based* helm: several
behaviors, each with a priority weight and run conditions, read a shared database (the MOOSDB) and
propose what the vehicle should do; the helm decides which behavior is in charge every iteration,
and behaviors raise *flags* (endflags) when they finish. This module keeps that shape, minus the
interval-programming solver (the winner takes the helm instead of being blended):

    Blackboard (MOOSDB)      nav position, ANOMALY_ALERT mailbox, SURVEY_COMPLETE flag
    BHV_StandardSurvey       priority  10  lawnmower waypoints            -> mode STANDARD_SURVEY
    BHV_AnomalyMapping       priority 100  runs while an alert is pending -> mode ADAPTIVE_MAPPING
                             (transit + dense inward spiral round the anomaly, then ENDFLAG)
    BHV_StationKeep          priority   1  always runnable, holds station -> mode SURVEY_COMPLETE

When the fusion core raises a high-confidence alert, BHV_AnomalyMapping becomes runnable, outranks the
survey, and the helm *suspends* the survey, remembering the exact point where it was interrupted.
After the spiral completes the behavior ends, the helm hands control back, and the survey resumes by
rejoining its track line at that point - no operator involved. Alerts that arrive during a spiral queue up.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from shared.config import SurveyConfig
from shared.geo import bearing_deg, local_to_geo
from shared.schemas import Anomaly

from .simulator import Waypoint

STANDARD_SURVEY = "STANDARD_SURVEY"
ADAPTIVE_MAPPING = "ADAPTIVE_MAPPING"
SURVEY_COMPLETE = "SURVEY_COMPLETE"

# Spiral parameters per anomaly type: (outer radius, inner radius) in metres
_SPIRAL_RADII = {"gas_plume": (70.0, 20.0), "shallow_gas": (55.0, 15.0)}


# ----------------------------------------------------------------------------- path generators
def lawnmower(cfg: SurveyConfig) -> list[Waypoint]:
    """Boustrophedon lines across the block, east-west, stepping north."""
    xs = (cfg.line_margin_m, cfg.width_m - cfg.line_margin_m)
    y, i, wps = 50.0, 0, []
    while y <= cfg.height_m - 30.0 + 1e-6:
        a, b = (xs if i % 2 == 0 else xs[::-1])
        wps += [Waypoint(a, y, cfg.cruise_speed_mps, "survey", f"L{i + 1}"),
                Waypoint(b, y, cfg.cruise_speed_mps, "survey", f"L{i + 1}")]
        y += cfg.line_spacing_m
        i += 1
    return wps


def spiral(cx: float, cy: float, r_out: float, r_in: float, pitch: float, entry_bearing_deg: float,
           speed: float, step_m: float = 12.0, tag: str = "") -> list[Waypoint]:
    """Inward clockwise Archimedean spiral r = r_out - pitch * theta / 2pi, starting on ``entry_bearing``."""
    turns = (r_out - r_in) / pitch
    theta_max = 2 * math.pi * turns
    phi0 = math.radians(entry_bearing_deg)
    th, wps = 0.0, []
    while th <= theta_max:
        r = r_out - pitch * th / (2 * math.pi)
        phi = phi0 + th                         # bearing grows clockwise
        wps.append(Waypoint(cx + r * math.sin(phi), cy + r * math.cos(phi), speed, "spiral", tag))
        th += step_m / max(r, 1.0)
    return wps


# ----------------------------------------------------------------------------- helm plumbing
@dataclass
class PlannerEvent:
    level: str                       # info | warning | success
    message: str
    anomaly_id: Optional[str] = None
    kind: str = "planner"            # planner | route_changed | mapping_complete | survey_complete
    done_anomaly: Optional[str] = None


@dataclass
class Blackboard:
    """The helm's view of the world (the MOOSDB analogue)."""
    nav_x: float = 0.0
    nav_y: float = 0.0
    survey_complete: bool = False          # SURVEY_COMPLETE
    surveyed: list = field(default_factory=list)   # ANOMALY_SURVEYED history


class Behavior:
    """Base behavior: a priority, run conditions, and a proposal (the waypoint to steer to)."""
    name = "BHV_Base"
    priority = 0.0
    mode = STANDARD_SURVEY

    def __init__(self) -> None:
        self.state = "idle"                # idle | running | suspended | complete
        self.rev = 0                       # bumped whenever the plan changes (drives route updates)

    # conditions / lifecycle
    def runnable(self, bb: Blackboard) -> bool: return True
    def on_start(self, bb: Blackboard) -> list[PlannerEvent]: return []
    def on_suspend(self, bb: Blackboard) -> None: pass
    def iterate(self, bb: Blackboard) -> list[PlannerEvent]: return []
    # proposal
    def target(self) -> Optional[Waypoint]: return None
    def plan(self) -> list[Waypoint]: return []
    def detail(self) -> str: return ""


class StandardSurveyBehavior(Behavior):
    name, priority, mode = "BHV_StandardSurvey", 10.0, STANDARD_SURVEY

    def __init__(self, cfg: SurveyConfig):
        super().__init__()
        self.cfg = cfg
        self._wps: deque[Waypoint] = deque(lawnmower(cfg))
        self.total = len(self._wps)
        self._interrupted_at: Optional[tuple[float, float]] = None

    def runnable(self, bb): return bool(self._wps)

    def on_start(self, bb):
        events = []
        if self._interrupted_at is not None:            # resume: rejoin the track line where we left it
            x, y = self._interrupted_at
            self._wps.appendleft(Waypoint(x, y, self.cfg.cruise_speed_mps, "rejoin", "resume"))
            self._interrupted_at = None
            self.rev += 1
            events.append(PlannerEvent("info", "Helm: BHV_StandardSurvey resumes - rejoining track line "
                                       "(STANDARD_SURVEY)", kind="route_changed"))
        self.state = "running"
        return events

    def on_suspend(self, bb):
        self._interrupted_at = (bb.nav_x, bb.nav_y)
        self.state = "suspended"

    def iterate(self, bb):
        events = []
        while self._wps and math.hypot(self._wps[0].x - bb.nav_x, self._wps[0].y - bb.nav_y) <= self.cfg.capture_radius_m:
            self._wps.popleft()
            self.rev += 1
        if not self._wps:
            self.state = "complete"
            bb.survey_complete = True                   # ENDFLAG: SURVEY_COMPLETE = true
            events.append(PlannerEvent("success", "Survey complete - BHV_StationKeep holds position",
                                       kind="survey_complete"))
        return events

    def target(self): return self._wps[0] if self._wps else None

    def plan(self):
        wps = list(self._wps)
        if self._interrupted_at is not None:            # show where the survey will pick up again
            x, y = self._interrupted_at
            wps.insert(0, Waypoint(x, y, self.cfg.cruise_speed_mps, "rejoin", "resume"))
        return wps

    @property
    def lines_left(self) -> int:
        return sum(1 for w in self._wps if w.kind == "survey")

    def detail(self):
        return f"{self.total - self.lines_left}/{self.total} waypoints"


class AnomalyMappingBehavior(Behavior):
    name, priority, mode = "BHV_AnomalyMapping", 100.0, ADAPTIVE_MAPPING

    def __init__(self, cfg: SurveyConfig, pitch_m: float):
        super().__init__()
        self.cfg, self.pitch = cfg, pitch_m
        self.pending: list[Anomaly] = []               # ANOMALY_ALERT mailbox
        self.current: Optional[Anomaly] = None
        self._wps: deque[Waypoint] = deque()
        self._plan_len = 1
        self._seen: set[str] = set()
        self.done = 0

    # --- mail ---
    def accept(self, a: Anomaly) -> bool:
        if a.id in self._seen:
            return False
        self._seen.add(a.id)
        self.pending.append(a)
        return True

    def runnable(self, bb): return self.current is not None or bool(self.pending)

    def on_start(self, bb):
        self.state = "running"
        return self._begin_next(bb)

    def on_suspend(self, bb): self.state = "idle"

    def _begin_next(self, bb) -> list[PlannerEvent]:
        a = self.pending.pop(0)
        r_out, r_in = _SPIRAL_RADII[a.type]
        entry = bearing_deg(bb.nav_x - a.east_m, bb.nav_y - a.north_m)   # enter from the side we are on
        self._wps = deque(spiral(a.east_m, a.north_m, r_out, r_in, self.pitch, entry,
                                 self.cfg.spiral_speed_mps, tag=a.id))
        self._plan_len = len(self._wps)
        self.current = a
        self.rev += 1
        return [PlannerEvent("warning", f"Adaptive re-route: BHV_AnomalyMapping outranks BHV_StandardSurvey "
                             f"(priority {self.priority:.0f} > 10) - injecting {self._plan_len}-waypoint spiral "
                             f"({r_out:.0f}->{r_in:.0f} m, {self.pitch:.0f} m pitch) around {a.id} [{a.type}] "
                             f"at {a.lat:.5f}, {a.lon:.5f}", a.id, kind="route_changed")]

    def iterate(self, bb):
        events = []
        while self._wps and math.hypot(self._wps[0].x - bb.nav_x, self._wps[0].y - bb.nav_y) <= self.cfg.capture_radius_m:
            self._wps.popleft()
            self.rev += 1
        if self.current is not None and not self._wps:
            done = self.current
            self.current, self.done = None, self.done + 1
            bb.surveyed.append(done.id)                  # ENDFLAG: ANOMALY_SURVEYED = <id>
            events.append(PlannerEvent("success", f"High-resolution mapping of {done.id} complete",
                                       done.id, kind="mapping_complete", done_anomaly=done.id))
            if self.pending:                             # more alerts waiting - stay in charge
                events += self._begin_next(bb)
            else:
                self.state = "complete"
        return events

    def target(self): return self._wps[0] if self._wps else None
    def plan(self): return list(self._wps)

    @property
    def progress(self) -> float:
        return 0.0 if self.current is None else 1.0 - len(self._wps) / max(self._plan_len, 1)

    def detail(self):
        if self.current is None:
            return "no pending alerts"
        q = f" · {len(self.pending)} queued" if self.pending else ""
        return f"{self.current.id} · {self.progress:.0%}{q}"


class StationKeepBehavior(Behavior):
    """Lowest priority, always runnable: the vehicle holds when nothing else wants control."""
    name, priority, mode = "BHV_StationKeep", 1.0, SURVEY_COMPLETE

    def on_start(self, bb):
        self.state = "running"
        return []

    def on_suspend(self, bb): self.state = "idle"
    def detail(self): return "holding"


# ----------------------------------------------------------------------------- the helm
class IvPHelm:
    """Picks the highest-priority runnable behavior every iteration and hands it the vehicle."""

    def __init__(self, cfg: SurveyConfig, spiral_pitch_m: float = 15.0):
        self.cfg = cfg
        self.bb = Blackboard()
        self.survey = StandardSurveyBehavior(cfg)
        self.mapping = AnomalyMappingBehavior(cfg, spiral_pitch_m)
        self.hold = StationKeepBehavior()
        self.behaviors: list[Behavior] = [self.mapping, self.survey, self.hold]
        self.active: Optional[Behavior] = None
        self.iteration = 0
        self._switches = 0
        self.total_survey_wps = self.survey.total
        self._decide()

    # ---- queries ---------------------------------------------------------------------------
    @property
    def mode(self) -> str: return self.active.mode if self.active else STANDARD_SURVEY
    @property
    def active_anomaly(self) -> Optional[str]: return self.mapping.current.id if self.mapping.current else None
    @property
    def version(self) -> int: return self._switches + sum(b.rev for b in self.behaviors)

    def current_target(self) -> Optional[Waypoint]:
        return self.active.target() if self.active else None

    @property
    def route(self) -> list[Waypoint]:
        """Planned path for display: what the active behavior will fly, then what the survey resumes with."""
        if self.active is self.mapping:
            return self.mapping.plan() + self.survey.plan()
        return self.survey.plan() if self.active is self.survey else []

    def status(self) -> dict:
        bhv = []
        for b in self.behaviors:
            state = "running" if b is self.active else ("suspended" if b.state == "suspended" else
                    ("complete" if b.state == "complete" else "idle"))
            bhv.append({"name": b.name, "priority": b.priority, "state": state, "detail": b.detail()})
        done = self.survey.total - self.survey.lines_left
        return {"mode": self.mode, "active_behavior": self.active.name if self.active else None,
                "behaviors": bhv, "helm_iter": self.iteration, "active_anomaly": self.active_anomaly,
                "queued": [a.id for a in self.mapping.pending], "spirals_done": self.mapping.done,
                "remaining_wps": len(self.route), "survey_progress": round(done / max(self.survey.total, 1), 3),
                "map_progress": round(self.mapping.progress, 3) if self.active is self.mapping else 0.0}

    def route_geo(self) -> list[dict]:
        out = []
        for w in self.route:
            lat, lon = local_to_geo(w.x, w.y)
            out.append({"east_m": round(w.x, 1), "north_m": round(w.y, 1), "lat": float(lat), "lon": float(lon),
                        "kind": w.kind, "tag": w.tag})
        return out

    # ---- helm iteration --------------------------------------------------------------------
    def update(self, x: float, y: float) -> list[PlannerEvent]:
        """One helm iteration: post nav, let the active behavior act, then (re)decide who is in charge."""
        self.bb.nav_x, self.bb.nav_y = x, y
        self.iteration += 1
        events = self.active.iterate(self.bb) if self.active else []
        return events + self._decide()

    def handle_alert(self, anomaly: Anomaly, x: float, y: float) -> list[PlannerEvent]:
        """Mail an ANOMALY_ALERT to the helm; BHV_AnomalyMapping takes over (or queues behind a running spiral)."""
        self.bb.nav_x, self.bb.nav_y = x, y
        if not self.mapping.accept(anomaly):
            return []
        events = self._decide()
        if self.active is self.mapping and self.mapping.current is not None and self.mapping.current.id != anomaly.id:
            events.append(PlannerEvent("info", f"{anomaly.id} queued - mapping of {self.mapping.current.id} "
                                       "already in progress", anomaly.id))
        return events

    def _decide(self) -> list[PlannerEvent]:
        chosen = max((b for b in self.behaviors if b.runnable(self.bb)), key=lambda b: b.priority)
        if chosen is self.active:
            return []
        events: list[PlannerEvent] = []
        if self.active is not None:
            self.active.on_suspend(self.bb)
        self.active = chosen
        self._switches += 1
        events += chosen.on_start(self.bb)
        return events


# Backwards-compatible name used by the engine and tests
AdaptivePlanner = IvPHelm
