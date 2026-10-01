"""Sensor fusion + edge-AI anomaly engine.

Pipeline per tick
-----------------
1. **Spatial-temporal alignment** - every MBES / SBP ping is geo-referenced by interpolating the
   telemetry track at the ping's *own* timestamp (sensors run on different clocks/latencies), then
   MBES and SBP pings are paired by nearest time. The MBES nadir seabed pick is used to give SBP
   reflectors an absolute depth.
2. **Detectors** (lightweight, CPU-only, adaptive - suitable for edge deployment)
   * Gas plume  - connected-component analysis of mid-water backscatter above a *self-calibrating*
                   robust noise baseline (speckle rejected by blob-size filtering).
   * Shallow gas - SBP bright-spot (reflector amplitude jump) + acoustic blanking (penetration drop)
                   relative to a running baseline.
3. **Tracker** - detections are clustered in space, evidence is accumulated into a confidence,
   and a severity score is maintained. A one-shot ``alert`` event fires at high confidence.
"""
from __future__ import annotations

import base64
import bisect
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import ndimage, signal

from shared.geo import local_to_geo, wrap_deg
from shared.schemas import Anomaly, AnomalyEvent, MBESFrame, SBPFrame, Telemetry


@dataclass
class FusionConfig:
    max_pair_dt_s: float = 0.20          # max MBES<->SBP timestamp gap for pairing
    max_extrap_s: float = 0.25           # dead-reckoning allowance past the last telemetry
    warmup_frames: int = 15              # frames used to learn noise baselines
    # water column
    wc_mid_lo: float = 0.15              # mid-water window as a fraction of range-to-bottom
    wc_mid_hi: float = 0.88
    wc_k_sigma: float = 6.0
    wc_min_excess_db: float = 8.0
    wc_min_blob_px: int = 6
    # sub-bottom
    sbp_amp_ratio: float = 1.5           # bright-spot: strongest reflector vs baseline
    sbp_pen_ratio: float = 0.80          # blanking: penetration vs baseline
    sbp_min_score: float = 0.15
    # tracker
    assoc_radius_m: dict = field(default_factory=lambda: {"gas_plume": 50.0, "shallow_gas": 40.0})
    evidence_scale: float = 2.0          # confidence = 1 - exp(-sum(score)/scale)
    detect_conf: float = 0.35
    alert_conf: float = 0.80
    alert_severity: float = 40.0


@dataclass
class Pose:
    t: float
    x: float
    y: float
    depth: float
    heading: float


@dataclass
class Detection:
    type: str
    x: float
    y: float
    depth: float
    score: float          # 0..1 per-ping quality
    t: float


@dataclass
class FusedFrame:
    """Time-aligned, geo-referenced MBES+SBP product (also the dashboard's live payload)."""
    t: float
    pose: Pose
    payload: dict


# ----------------------------------------------------------------------------- alignment
class TrackBuffer:
    """Time-ordered telemetry with interpolation and short dead-reckoning extrapolation."""

    def __init__(self, maxlen: int = 400):
        self.t: list[float] = []
        self.s: list[Telemetry] = []
        self.maxlen = maxlen

    def add(self, tel: Telemetry) -> None:
        i = bisect.bisect(self.t, tel.t)
        self.t.insert(i, tel.t)
        self.s.insert(i, tel)
        if len(self.t) > self.maxlen:
            del self.t[0], self.s[0]

    @property
    def last_t(self) -> float:
        return self.t[-1] if self.t else -math.inf

    def pose_at(self, t: float, max_extrap: float) -> Optional[Pose]:
        if not self.t or t < self.t[0]:
            return None
        if t > self.t[-1]:
            dt = t - self.t[-1]
            if dt > max_extrap:
                return None
            a = self.s[-1]
            h = math.radians(a.heading_deg)
            return Pose(t, a.east_m + a.speed_mps * math.sin(h) * dt, a.north_m + a.speed_mps * math.cos(h) * dt,
                        a.depth_m, a.heading_deg)
        i = bisect.bisect_left(self.t, t)
        b = self.s[i]
        if self.t[i] == t or i == 0:
            return Pose(t, b.east_m, b.north_m, b.depth_m, b.heading_deg)
        a = self.s[i - 1]
        f = (t - a.t) / (b.t - a.t)
        return Pose(t, a.east_m + f * (b.east_m - a.east_m), a.north_m + f * (b.north_m - a.north_m),
                    a.depth_m + f * (b.depth_m - a.depth_m),
                    (a.heading_deg + f * wrap_deg(b.heading_deg - a.heading_deg)) % 360.0)


# ----------------------------------------------------------------------------- tracker
@dataclass
class _Track:
    id: str
    type: str
    wx: float = 0.0
    wy: float = 0.0
    wz: float = 0.0
    wsum: float = 0.0
    evidence: float = 0.0
    hits: int = 0
    sev: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0
    announced: bool = False
    alerted: bool = False
    status: str = "candidate"

    @property
    def x(self): return self.wx / self.wsum
    @property
    def y(self): return self.wy / self.wsum
    @property
    def z(self): return self.wz / self.wsum


class AnomalyTracker:
    def __init__(self, cfg: FusionConfig):
        self.cfg = cfg
        self.tracks: list[_Track] = []
        self.version = 0

    def update(self, d: Detection) -> list[AnomalyEvent]:
        cfg = self.cfg
        radius = cfg.assoc_radius_m[d.type]
        best, best_d = None, radius
        for tr in self.tracks:
            if tr.type == d.type:
                dist = math.hypot(tr.x - d.x, tr.y - d.y)
                if dist < best_d:
                    best, best_d = tr, dist
        if best is None:
            best = _Track(id=f"ANOM-{len(self.tracks) + 1:03d}", type=d.type, first_seen=d.t)
            self.tracks.append(best)
        tr = best
        w = max(d.score, 1e-3)
        tr.wx += w * d.x; tr.wy += w * d.y; tr.wz += w * d.depth; tr.wsum += w
        tr.evidence += d.score
        tr.hits += 1
        tr.last_seen = d.t
        tr.sev = d.score * 100 if tr.hits == 1 else max(tr.sev * 0.9, 0.4 * d.score * 100 + 0.6 * tr.sev)
        self.version += 1

        events: list[AnomalyEvent] = []
        conf = self.confidence(tr)
        if not tr.announced and tr.hits >= 2 and conf >= cfg.detect_conf:
            tr.announced = True
            events.append(AnomalyEvent(kind="detected", anomaly=self.to_model(tr)))
        if not tr.alerted and conf >= cfg.alert_conf and tr.sev >= cfg.alert_severity:
            tr.alerted, tr.status = True, "confirmed"
            events.append(AnomalyEvent(kind="alert", anomaly=self.to_model(tr)))
        return events

    def confidence(self, tr: _Track) -> float:
        return 1.0 - math.exp(-tr.evidence / self.cfg.evidence_scale)

    def mark_surveyed(self, anomaly_id: str) -> None:
        for tr in self.tracks:
            if tr.id == anomaly_id:
                tr.status = "surveyed"
                self.version += 1

    def to_model(self, tr: _Track) -> Anomaly:
        lat, lon = local_to_geo(tr.x, tr.y)
        return Anomaly(id=tr.id, type=tr.type, lat=float(lat), lon=float(lon), east_m=tr.x, north_m=tr.y,
                       depth_m=tr.z, severity=round(min(tr.sev, 100.0), 1), confidence=round(self.confidence(tr), 3),
                       hits=tr.hits, first_seen=tr.first_seen, last_seen=tr.last_seen, status=tr.status,
                       radius_m=45.0 if tr.type == "gas_plume" else 30.0)

    def snapshot(self) -> list[Anomaly]:
        return [self.to_model(t) for t in self.tracks if t.announced]


# ----------------------------------------------------------------------------- core
@dataclass
class FusionOutput:
    frames: list[FusedFrame] = field(default_factory=list)
    events: list[AnomalyEvent] = field(default_factory=list)


class FusionCore:
    def __init__(self, cfg: FusionConfig | None = None):
        self.cfg = cfg or FusionConfig()
        self.track = TrackBuffer()
        self.tracker = AnomalyTracker(self.cfg)
        self._pend_mbes: list[MBESFrame] = []
        self._pend_sbp: list[SBPFrame] = []
        self._ready_mbes: list[tuple[MBESFrame, Pose]] = []
        self._ready_sbp: list[tuple[SBPFrame, Pose]] = []
        # adaptive baselines
        self._wc_mu: Optional[float] = None
        self._wc_sd: float = 1.5
        self._wc_n = 0
        self._last_mid_peak: Optional[float] = None
        self._amp_b: Optional[float] = None
        self._pen_b: Optional[float] = None
        self._sbp_n = 0

    # ---- input --------------------------------------------------------------------------
    def ingest(self, frame) -> None:
        if isinstance(frame, Telemetry):
            self.track.add(frame)
        elif isinstance(frame, MBESFrame):
            self._pend_mbes.append(frame)
        elif isinstance(frame, SBPFrame):
            self._pend_sbp.append(frame)

    # ---- processing ---------------------------------------------------------------------
    def process(self) -> FusionOutput:
        out = FusionOutput()
        self._georeference()
        cfg = self.cfg
        while self._ready_mbes or self._ready_sbp:
            m = self._ready_mbes[0] if self._ready_mbes else None
            s = self._ready_sbp[0] if self._ready_sbp else None
            if m and s:
                dt = s[0].t - m[0].t
                if abs(dt) <= cfg.max_pair_dt_s:
                    self._ready_mbes.pop(0); self._ready_sbp.pop(0)
                    self._emit(m, s, out)
                elif dt > 0:
                    self._ready_mbes.pop(0); self._emit(m, None, out)
                else:
                    self._ready_sbp.pop(0); self._emit(None, s, out)
            else:
                break   # wait for the partner frame
        # flush frames that have waited too long for a partner
        newest = self.track.last_t
        while self._ready_mbes and newest - self._ready_mbes[0][0].t > 1.0:
            self._emit(self._ready_mbes.pop(0), None, out)
        while self._ready_sbp and newest - self._ready_sbp[0][0].t > 1.0:
            self._emit(None, self._ready_sbp.pop(0), out)
        return out

    def _georeference(self) -> None:
        for pend, ready in ((self._pend_mbes, self._ready_mbes), (self._pend_sbp, self._ready_sbp)):
            keep = []
            for f in pend:
                pose = self.track.pose_at(f.t, self.cfg.max_extrap_s)
                if pose is not None:
                    ready.append((f, pose))
                elif f.t > self.track.last_t:      # telemetry not here yet - wait
                    keep.append(f)                 # (frames older than the buffer are dropped)
            pend[:] = keep

    def _emit(self, m, s, out: FusionOutput) -> None:
        det_p = self._detect_plume(m[0], m[1]) if m else None
        nadir = float(np.median(np.asarray(m[0].depth_m)[len(m[0].depth_m) // 2 - 1: len(m[0].depth_m) // 2 + 2])) \
            if m else None
        det_g = self._detect_gas(s[0], s[1], nadir) if s else None
        for d in (det_p, det_g):
            if d is not None:
                out.events.extend(self.tracker.update(d))
        pose = (m or s)[1]
        out.frames.append(FusedFrame(t=pose.t, pose=pose, payload=self._payload(m, s, nadir)))

    # ---- detectors ----------------------------------------------------------------------
    def _detect_plume(self, f: MBESFrame, pose: Pose) -> Optional[Detection]:
        cfg = self.cfg
        wc = np.asarray(f.water_column_db)
        ang = np.radians(f.beam_angles_deg)
        r = (np.arange(wc.shape[1]) + 0.5) * f.wc_bin_m
        r_bot = (np.asarray(f.depth_m) - pose.depth) / np.cos(ang)
        mask = (r[None, :] > cfg.wc_mid_lo * r_bot[:, None]) & (r[None, :] < cfg.wc_mid_hi * r_bot[:, None])
        vals = wc[mask]
        if vals.size < 50:
            return None
        self._last_mid_peak = float(vals.max())
        med = float(np.median(vals))
        mad = float(np.median(np.abs(vals - med))) * 1.4826

        if self._wc_mu is None:
            self._wc_mu, self._wc_sd = med, max(mad, 0.5)
        thr = self._wc_mu + max(cfg.wc_k_sigma * self._wc_sd, cfg.wc_min_excess_db)

        det = None
        if self._wc_n >= cfg.warmup_frames:
            lab, n = ndimage.label(mask & (wc > thr))
            if n:
                sizes = ndimage.sum(np.ones_like(wc), lab, index=np.arange(1, n + 1))
                k = int(np.argmax(sizes)) + 1
                if sizes[k - 1] >= cfg.wc_min_blob_px:
                    blob = lab == k
                    excess = np.where(blob, wc - self._wc_mu, 0.0)
                    peak = float(excess.max())
                    w = np.clip(excess, 0, None)
                    bi, ri = np.nonzero(blob)
                    cw = w[bi, ri]
                    rr = r[ri]
                    across = float(np.average(rr * np.sin(ang[bi]), weights=cw))
                    vert_all = rr * np.cos(ang[bi])
                    vert = float(np.average(vert_all, weights=cw))
                    extent = float(vert_all.max() - vert_all.min())
                    h = math.radians(pose.heading)
                    score = (0.55 * min(peak / 25.0, 1) + 0.25 * min(extent / 50.0, 1)
                             + 0.20 * min(float(sizes[k - 1]) / 60.0, 1))
                    det = Detection("gas_plume", pose.x + across * math.cos(h), pose.y - across * math.sin(h),
                                    pose.depth + vert, float(min(score, 1.0)), pose.t)
        if det is None:   # learn the baseline only from clean pings
            a = 0.05 if self._wc_n >= cfg.warmup_frames else 0.3
            self._wc_mu += a * (med - self._wc_mu)
            self._wc_sd += a * (max(mad, 0.5) - self._wc_sd)
        self._wc_n += 1
        return det

    def _detect_gas(self, f: SBPFrame, pose: Pose, nadir_depth: Optional[float]) -> Optional[Detection]:
        cfg = self.cfg
        tr = np.asarray(f.trace)
        z = np.arange(tr.size) * f.trace_dz_m
        sub = z > 1.5
        pk, _ = signal.find_peaks(np.where(sub, tr, 0.0), height=0.15, prominence=0.1,
                                  distance=max(1, int(1.5 / f.trace_dz_m)))
        if pk.size == 0:
            return None
        top = pk[np.argmax(tr[pk])]
        amp, pen = float(tr[top]), f.penetration_depth_m

        if self._amp_b is None:
            self._amp_b, self._pen_b = amp, pen
        r_amp, r_pen = amp / self._amp_b, pen / self._pen_b
        det = None
        if self._sbp_n >= cfg.warmup_frames and (r_amp > cfg.sbp_amp_ratio or r_pen < cfg.sbp_pen_ratio):
            s1 = min(max((r_amp - 1.3) / 1.5, 0.0), 1.0)
            s2 = min(max((0.88 - r_pen) / 0.3, 0.0), 1.0)
            score = min(0.7 * s1 + 0.5 * s2, 1.0)
            if score >= cfg.sbp_min_score:
                seabed = nadir_depth if nadir_depth is not None else f.seabed_depth_m
                det = Detection("shallow_gas", pose.x, pose.y, seabed + float(z[top]), score, pose.t)
        if det is None:
            a = 0.05 if self._sbp_n >= cfg.warmup_frames else 0.3
            self._amp_b += a * (amp - self._amp_b)
            self._pen_b += a * (pen - self._pen_b)
        self._sbp_n += 1
        return det

    # ---- dashboard payload --------------------------------------------------------------
    def _payload(self, m, s, nadir) -> dict:
        p: dict = {}
        if m:
            f, pose = m
            ang = np.radians(f.beam_angles_deg)
            depth = np.asarray(f.depth_m)
            across = (depth - pose.depth) * np.tan(ang)
            h = math.radians(pose.heading)
            p["swath"] = {
                "across_m": across.round(1).tolist(), "depth_m": depth.round(2).tolist(),
                "east_m": (pose.x + across * math.cos(h)).round(1).tolist(),
                "north_m": (pose.y - across * math.sin(h)).round(1).tolist(),
                "backscatter_db": np.asarray(f.backscatter_db).round(1).tolist(),
            }
            c = len(f.backscatter_db) // 2
            p["metrics"] = {"bs_nadir_db": round(float(np.mean(f.backscatter_db[c - 1:c + 2])), 1),
                            "wc_peak_db": None if self._last_mid_peak is None else round(self._last_mid_peak, 1),
                            "wc_floor_db": None if self._wc_mu is None else round(self._wc_mu, 1)}
            wc = np.asarray(f.water_column_db)
            q = np.clip(wc[:, : wc.shape[1] // 2 * 2].reshape(wc.shape[0], -1, 2).max(axis=2) + 80.0, 0, 99)
            p["wc_fan"] = {"beams": int(q.shape[0]), "bins": int(q.shape[1]), "bin_m": f.wc_bin_m * 2,
                           "angles_deg": f.beam_angles_deg, "veh_depth_m": round(pose.depth, 2),
                           "b64": base64.b64encode(q.astype(np.uint8).tobytes()).decode()}
        if s:
            f, pose = s
            tr = np.asarray(f.trace)
            z = np.arange(tr.size) * f.trace_dz_m
            seabed = nadir if nadir is not None else f.seabed_depth_m
            pk, _ = signal.find_peaks(np.where(z > 1.5, tr, 0.0), height=0.15, prominence=0.1,
                                      distance=max(1, int(1.5 / f.trace_dz_m)))
            p["sbp"] = {"trace": tr.round(2).tolist(), "dz_m": f.trace_dz_m, "seabed_m": round(seabed, 2),
                        "penetration_m": f.penetration_depth_m, "east_m": round(pose.x, 1), "north_m": round(pose.y, 1),
                        "reflectors": [{"depth_m": round(seabed + float(z[i]), 2), "amp": round(float(tr[i]), 2)}
                                       for i in pk]}
        return p
