"""Pydantic wire contracts for sensor frames, anomalies and planner status.

Sensor frames carry a ``kind`` discriminator so a single ingestion endpoint can accept
any of them. Depths are positive-down metres; times are epoch seconds (simulation clock).
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class Telemetry(BaseModel):
    kind: Literal["telemetry"] = "telemetry"
    t: float
    lat: float
    lon: float
    east_m: float
    north_m: float
    depth_m: float
    heading_deg: float
    speed_mps: float


class MBESFrame(BaseModel):
    """One multibeam ping: bathymetry, backscatter and a water-column image."""
    kind: Literal["mbes"] = "mbes"
    t: float
    ping_id: int
    beam_angles_deg: list[float]            # +ve = starboard
    depth_m: list[float]                    # seabed depth per beam
    backscatter_db: list[float]             # seabed backscatter per beam
    wc_bin_m: float                         # slant-range bin size
    water_column_db: list[list[float]]      # [beam][range bin] acoustic return


class SBPFrame(BaseModel):
    """One sub-bottom profiler ping (nadir)."""
    kind: Literal["sbp"] = "sbp"
    t: float
    ping_id: int
    seabed_depth_m: float                   # SBP's own seabed pick
    trace_dz_m: float                       # sample spacing below seabed
    trace: list[float]                      # amplitude vs depth below seabed
    penetration_depth_m: float              # deepest coherent return below seabed
    reflector_amplitude: float              # strongest sub-bottom reflector amplitude


SensorFrame = Annotated[Union[Telemetry, MBESFrame, SBPFrame], Field(discriminator="kind")]

AnomalyType = Literal["gas_plume", "shallow_gas"]


class Anomaly(BaseModel):
    id: str
    type: AnomalyType
    lat: float
    lon: float
    east_m: float
    north_m: float
    depth_m: float                          # absolute depth of the feature (plume core / gas reflector)
    severity: float                         # 0-100
    confidence: float                       # 0-1
    hits: int
    first_seen: float
    last_seen: float
    status: Literal["candidate", "confirmed", "surveyed"] = "candidate"
    radius_m: float = 40.0


class AnomalyEvent(BaseModel):
    """Emitted by the fusion core: ``detected`` (first candidate), ``alert`` (high confidence)."""
    kind: Literal["detected", "alert"]
    anomaly: Anomaly
