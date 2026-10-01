"""Small geodesy helpers: local ENU metres <-> WGS-84 (equirectangular, fine for a few km)."""
from __future__ import annotations

import math

import numpy as np

from .config import ORIGIN_LAT, ORIGIN_LON

M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON = M_PER_DEG_LAT * math.cos(math.radians(ORIGIN_LAT))


def local_to_geo(x, y):
    """East/north metres from ORIGIN -> (lat, lon). Works on scalars and numpy arrays."""
    return ORIGIN_LAT + np.asarray(y) / M_PER_DEG_LAT, ORIGIN_LON + np.asarray(x) / _M_PER_DEG_LON


def geo_to_local(lat, lon):
    """(lat, lon) -> east/north metres from ORIGIN."""
    return (np.asarray(lon) - ORIGIN_LON) * _M_PER_DEG_LON, (np.asarray(lat) - ORIGIN_LAT) * M_PER_DEG_LAT


def bearing_deg(dx: float, dy: float) -> float:
    """Compass bearing (0 = north, clockwise) of an east/north offset."""
    return math.degrees(math.atan2(dx, dy)) % 360.0


def wrap_deg(a: float) -> float:
    """Wrap an angle to (-180, 180]."""
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a
