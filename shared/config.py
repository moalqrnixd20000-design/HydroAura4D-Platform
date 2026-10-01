"""Scenario & system configuration shared by every backend module.

All geometry is expressed in a local ENU frame (metres east / north of ORIGIN)
and converted to WGS-84 lat/lon only at the API boundary (see shared.geo).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Survey origin (south-west corner of the block) - Red Sea, off the Egyptian coast.
ORIGIN_LAT = 27.0000
ORIGIN_LON = 34.0500


@dataclass(frozen=True)
class SurveyConfig:
    # --- survey block (local metres) ---
    width_m: float = 600.0          # east extent
    height_m: float = 400.0         # north extent
    line_spacing_m: float = 100.0   # lawnmower line spacing
    line_margin_m: float = 20.0     # inset of the survey lines from the block edge
    start_xy: tuple[float, float] = (-60.0, 50.0)   # AUV launch point (outside the block)

    # --- vehicle ---
    cruise_speed_mps: float = 12.0
    spiral_speed_mps: float = 6.0   # slower => denser pings per metre of track
    max_turn_rate_dps: float = 50.0
    max_accel_mps2: float = 2.0
    capture_radius_m: float = 14.0
    vehicle_depth_m: float = 15.0   # constant cruise depth (AUV running shallow over the shelf)

    # --- clock ---
    tick_dt_s: float = 0.25         # simulated seconds between sensor ticks (4 Hz)
    time_scale: float = 4.0         # simulated seconds per wall-clock second

    # --- MBES ---
    n_beams: int = 31
    beam_fan_deg: float = 50.0      # +/- half-angle of the swath
    wc_bins: int = 120
    wc_bin_m: float = 1.5

    # --- SBP ---
    sbp_samples: int = 160
    sbp_dz_m: float = 0.25          # depth sampling below seabed => 40 m trace

    seed: int = 7


@dataclass(frozen=True)
class GasPlumeTruth:
    """Simulated ground-truth water-column gas seep (hidden from the algorithms)."""
    x: float = 230.0
    y: float = 105.0
    height_m: float = 75.0          # rise height above seabed
    peak_db: float = 28.0           # peak backscatter excess over noise floor


@dataclass(frozen=True)
class ShallowGasTruth:
    """Simulated ground-truth shallow gas pocket / hydrocarbon trap below the seabed."""
    x: float = 430.0
    y: float = 250.0
    radius_m: float = 22.0          # Gaussian sigma of the lateral footprint
    depth_bsb_m: float = 15.0       # depth below seabed of the gas reflector


@dataclass(frozen=True)
class Scenario:
    survey: SurveyConfig = field(default_factory=SurveyConfig)
    plume: GasPlumeTruth | None = field(default_factory=GasPlumeTruth)
    pocket: ShallowGasTruth | None = field(default_factory=ShallowGasTruth)
