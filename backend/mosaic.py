"""Server-side seabed mosaic: gridded depth + backscatter accumulated from MBES soundings.

The dashboard builds the same grid live from the tick stream; this copy lets a browser that connects
(or refreshes) mid-mission recover the whole map-so-far from the ``hello`` snapshot.
"""
from __future__ import annotations

import base64

import numpy as np

X0, Y0, CELL, NX, NY = -120.0, -120.0, 4.0, 210, 160      # covers the block plus swath overhang
DMIN, DMAX = 100.0, 140.0                                   # depth colour range (m)
BMIN, BMAX = -45.0, -10.0                                   # backscatter colour range (dB)
_W = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], dtype=np.float32)   # 3x3 splat kernel


class SeabedMosaic:
    def __init__(self) -> None:
        self.n = np.zeros((NY, NX), np.float32)
        self.d = np.zeros((NY, NX), np.float32)
        self.b = np.zeros((NY, NX), np.float32)

    def add(self, east, north, depth, bs) -> None:
        east, north, depth, bs = (np.asarray(a, dtype=np.float64) for a in (east, north, depth, bs))
        ix = np.floor((east - X0) / CELL).astype(int)
        iy = np.floor((north - Y0) / CELL).astype(int)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                jx, jy = ix + dx, iy + dy
                ok = (jx >= 0) & (jx < NX) & (jy >= 0) & (jy < NY)
                w = _W[dy + 1, dx + 1]
                np.add.at(self.n, (jy[ok], jx[ok]), w)
                np.add.at(self.d, (jy[ok], jx[ok]), w * depth[ok])
                np.add.at(self.b, (jy[ok], jx[ok]), w * bs[ok])

    @staticmethod
    def _quant(sum_, n, lo, hi) -> bytes:
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(n > 0.5, sum_ / np.maximum(n, 1e-6), np.nan)
        q = np.where(np.isnan(mean), 0, 1 + np.clip((mean - lo) / (hi - lo), 0, 1) * 254)
        return q.astype(np.uint8).tobytes()

    def encode(self) -> dict:
        return {"x0": X0, "y0": Y0, "cell": CELL, "nx": NX, "ny": NY, "dmin": DMIN, "dmax": DMAX,
                "bmin": BMIN, "bmax": BMAX,
                "depth_b64": base64.b64encode(self._quant(self.d, self.n, DMIN, DMAX)).decode(),
                "bs_b64": base64.b64encode(self._quant(self.b, self.n, BMIN, BMAX)).decode()}
