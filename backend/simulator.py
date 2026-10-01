"""Synthetic AUV survey: seabed world model, vehicle kinematics, MBES / SBP / telemetry generators.

The simulator is the *only* module that knows the hidden ground truth (gas plume, gas pocket).
Everything downstream sees nothing but sensor frames, exactly as with a real vehicle.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import numpy as np

from shared.config import Scenario, SurveyConfig
from shared.geo import bearing_deg, local_to_geo, wrap_deg
from shared.schemas import MBESFrame, SBPFrame, Telemetry

# Nominal sub-bottom reflectors: (mean depth below seabed [m], amplitude)
_LAYER_AMPS = (0.80, 0.60, 0.45)
_SEABED_RETURN_AMP = 1.6
_WC_NOISE_FLOOR_DB = -75.0


def _gauss(z: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    return np.exp(-0.5 * ((z - mu) / sigma) ** 2)


class World:
    """Analytic seabed + sub-bottom + anomaly model (deterministic, smooth)."""

    def __init__(self, scn: Scenario):
        self.scn = scn

    # ---- bathymetry -------------------------------------------------------------------
    def seabed_depth(self, x, y):
        z = (110.0 + 0.02 * x + 0.012 * y
             + 3.0 * np.sin(x / 90.0) * np.cos(y / 70.0)
             + 1.2 * np.sin(x / 37.0 + 1.0))
        p = self.scn.plume
        if p is not None:  # pockmark at the seep vent
            z = z + 4.0 * np.exp(-((x - p.x) ** 2 + (y - p.y) ** 2) / (2 * 18.0 ** 2))
        return z

    def backscatter_base_db(self, x, y):
        bs = -24.0 + 2.0 * np.sin(x / 50.0) * np.cos(y / 60.0)
        for a in (self.scn.plume, self.scn.pocket):  # hard carbonate crust near seeps
            if a is not None:
                bs = bs + 6.0 * np.exp(-((x - a.x) ** 2 + (y - a.y) ** 2) / (2 * 45.0 ** 2))
        return bs

    # ---- sub-bottom -------------------------------------------------------------------
    def layer_depths(self, x: float, y: float) -> list[float]:
        return [4.0 + 1.0 * math.sin(x / 120.0),
                9.0 + 1.5 * math.sin(y / 100.0 + 1.0) + 0.004 * x,
                24.0 + 1.5 * math.cos(x / 150.0)]

    def pocket_weight(self, x: float, y: float) -> float:
        k = self.scn.pocket
        if k is None:
            return 0.0
        return math.exp(-((x - k.x) ** 2 + (y - k.y) ** 2) / (2 * k.radius_m ** 2))


@dataclass
class Waypoint:
    x: float
    y: float
    speed: float
    kind: str = "survey"   # survey | spiral | rejoin | transit
    tag: str = ""


class Vehicle:
    """Kinematic AUV: turn-rate and acceleration limited, flies to the current waypoint."""

    def __init__(self, cfg: SurveyConfig):
        self.cfg = cfg
        self.x, self.y = cfg.start_xy
        self.heading = 90.0          # start heading east
        self.speed = 0.0
        self.depth = cfg.vehicle_depth_m

    def step(self, dt: float, target: Optional[Waypoint]) -> None:
        c = self.cfg
        if target is None:
            want_speed = 0.0
        else:
            err = wrap_deg(bearing_deg(target.x - self.x, target.y - self.y) - self.heading)
            lim = c.max_turn_rate_dps * dt
            self.heading = (self.heading + max(-lim, min(lim, err))) % 360.0
            want_speed = target.speed
        dv = max(-c.max_accel_mps2 * dt, min(c.max_accel_mps2 * dt, want_speed - self.speed))
        self.speed += dv
        h = math.radians(self.heading)
        self.x += self.speed * math.sin(h) * dt
        self.y += self.speed * math.cos(h) * dt


class SurveySimulator:
    """Generates telemetry + MBES + SBP for a vehicle flying whatever waypoints it is given."""

    def __init__(self, scenario: Scenario | None = None):
        self.scn = scenario or Scenario()
        self.cfg = self.scn.survey
        self.world = World(self.scn)
        self.rng = np.random.default_rng(self.cfg.seed)
        self.vehicle = Vehicle(self.cfg)
        self.t0 = time.time()
        self.sim_t = 0.0
        self.ping_id = 0
        c = self.cfg
        self._angles = np.radians(np.linspace(-c.beam_fan_deg, c.beam_fan_deg, c.n_beams))
        self._r_bins = (np.arange(c.wc_bins) + 0.5) * c.wc_bin_m
        self._z_trace = np.arange(c.sbp_samples) * c.sbp_dz_m

    # ------------------------------------------------------------------ public API
    def truth(self) -> dict:
        """Ground truth for the optional 'show simulated truth' overlay (never used by algorithms)."""
        out = {}
        if self.scn.plume:
            p = self.scn.plume
            lat, lon = local_to_geo(p.x, p.y)
            out["plume"] = dict(east_m=p.x, north_m=p.y, lat=float(lat), lon=float(lon),
                                seabed_m=float(self.world.seabed_depth(p.x, p.y)), height_m=p.height_m)
        if self.scn.pocket:
            k = self.scn.pocket
            lat, lon = local_to_geo(k.x, k.y)
            out["pocket"] = dict(east_m=k.x, north_m=k.y, lat=float(lat), lon=float(lon),
                                 seabed_m=float(self.world.seabed_depth(k.x, k.y)),
                                 depth_bsb_m=k.depth_bsb_m, radius_m=k.radius_m)
        return out

    def step(self, target: Optional[Waypoint]) -> tuple[Telemetry, MBESFrame, SBPFrame]:
        """Advance one tick toward ``target`` and return the three sensor frames."""
        dt = self.cfg.tick_dt_s
        self.vehicle.step(dt, target)
        self.sim_t += dt
        self.ping_id += 1
        t = self.t0 + self.sim_t
        return self._telemetry(t), self._mbes(t + self.rng.uniform(-0.03, 0.03)), \
            self._sbp(t + 0.04 + self.rng.uniform(-0.02, 0.02))

    async def stream(self, route_provider) -> AsyncIterator[tuple[Telemetry, MBESFrame, SBPFrame]]:
        """Real-time async generator (paced by ``time_scale``). ``route_provider()`` -> current Waypoint."""
        import asyncio
        interval = self.cfg.tick_dt_s / self.cfg.time_scale
        nxt = time.monotonic()
        while True:
            yield self.step(route_provider())
            nxt += interval
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))

    # ------------------------------------------------------------------ generators
    def _telemetry(self, t: float) -> Telemetry:
        v = self.vehicle
        lat, lon = local_to_geo(v.x, v.y)
        return Telemetry(t=t, lat=float(lat), lon=float(lon), east_m=v.x, north_m=v.y,
                         depth_m=v.depth, heading_deg=v.heading, speed_mps=v.speed)

    def _mbes(self, t: float) -> MBESFrame:
        c, v, w, rng = self.cfg, self.vehicle, self.world, self.rng
        h = math.radians(v.heading)
        stbd = np.array([math.cos(h), -math.sin(h)])            # starboard unit vector (E, N)
        sin_a, cos_a, tan_a = np.sin(self._angles), np.cos(self._angles), np.tan(self._angles)

        # Seabed footprint per beam (two fixed-point iterations on the ground position)
        zv = np.full_like(self._angles, float(w.seabed_depth(v.x, v.y)) - v.depth)
        for _ in range(2):
            off = zv * tan_a
            gx, gy = v.x + off * stbd[0], v.y + off * stbd[1]
            seabed = w.seabed_depth(gx, gy)
            zv = seabed - v.depth
        depth = seabed + rng.normal(0, 0.05, seabed.shape) + 0.002 * zv * rng.normal(size=seabed.shape)
        bs = (w.backscatter_base_db(gx, gy) - 0.25 * np.abs(np.degrees(self._angles))
              + rng.normal(0, 1.2, seabed.shape))

        # Water column image [beam, bin]
        r = self._r_bins[None, :]
        wc = _WC_NOISE_FLOOR_DB + rng.normal(0, 1.5, (c.n_beams, c.wc_bins))
        vert = r * cos_a[:, None]                       # metres below vehicle
        across = r * sin_a[:, None]
        px, py = v.x + across * stbd[0], v.y + across * stbd[1]
        p = self.scn.plume
        if p is not None:
            zs = float(w.seabed_depth(p.x, p.y))
            h_above = zs - (v.depth + vert)
            inside = (h_above > 0) & (h_above < p.height_m)
            rad = 6.0 + 0.1 * np.clip(h_above, 0, None)
            d2 = (px - p.x) ** 2 + (py - p.y) ** 2
            flicker = 1.0 + 0.12 * np.sin(2 * math.pi * self.sim_t / 3.1 + 0.3 * np.arange(c.wc_bins))[None, :]
            wc += np.where(inside, p.peak_db * np.exp(-d2 / (2 * rad ** 2)) *
                           (1 - 0.45 * np.clip(h_above, 0, None) / p.height_m) * flicker, 0.0)
        r_bottom = (zv / cos_a)[:, None]
        wc += 35.0 * np.exp(-((r - r_bottom) / 2.0) ** 2)               # seabed return
        wc = np.where(r > r_bottom + 6.0, _WC_NOISE_FLOOR_DB + 4 + rng.normal(0, 1.5, wc.shape), wc)
        if rng.random() < 0.04:                                          # speckle / single fish
            b, k = rng.integers(0, c.n_beams), rng.integers(10, 60)
            wc[b, k] += 14.0

        return MBESFrame(t=t, ping_id=self.ping_id,
                         beam_angles_deg=np.degrees(self._angles).round(3).tolist(),
                         depth_m=depth.round(3).tolist(), backscatter_db=bs.round(2).tolist(),
                         wc_bin_m=c.wc_bin_m, water_column_db=wc.round(1).tolist())

    def _sbp(self, t: float) -> SBPFrame:
        c, v, w, rng = self.cfg, self.vehicle, self.world, self.rng
        z = self._z_trace
        seabed = float(w.seabed_depth(v.x, v.y)) + rng.normal(0, 0.05)
        wgt = w.pocket_weight(v.x, v.y)
        k = self.scn.pocket
        gas_d = (k.depth_bsb_m + 0.4 * math.sin(v.x / 60.0)) if k else 1e9

        trace = _SEABED_RETURN_AMP * _gauss(z, 0.0, 0.5)
        for d, a in zip(w.layer_depths(v.x, v.y), _LAYER_AMPS):
            amp = a * (1 - 0.8 * wgt) if d > gas_d else a        # acoustic blanking under gas
            trace += amp * _gauss(z, d, 0.5)
        if wgt > 1e-3:
            trace += 3.0 * wgt * _gauss(z, gas_d, 0.6)             # bright spot
        trace += rng.normal(0, 0.03, z.shape)

        sub = z > 1.5
        refl_amp = float(trace[sub].max())
        env = np.convolve(np.abs(trace), np.ones(3) / 3, mode="same")
        deep = np.flatnonzero((env > 0.15) & sub)
        pen = float(z[deep[-1]] + 1.0) if deep.size else 0.0
        return SBPFrame(t=t, ping_id=self.ping_id, seabed_depth_m=round(seabed, 3), trace_dz_m=c.sbp_dz_m,
                        trace=trace.round(3).tolist(), penetration_depth_m=round(pen, 2),
                        reflector_amplitude=round(refl_amp, 3))
