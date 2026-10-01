"""Ingestion: validates raw sensor messages and fans them out to subscribers.

The same hub accepts frames from the in-process simulator *and* from external producers
(``POST /api/ingest``), so a real vehicle gateway can replace the simulator unchanged.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Callable, Union

from pydantic import BaseModel, TypeAdapter, ValidationError

from shared.schemas import MBESFrame, SBPFrame, SensorFrame, Telemetry

log = logging.getLogger("hydroaura.ingestion")
_ADAPTER = TypeAdapter(SensorFrame)

Frame = Union[Telemetry, MBESFrame, SBPFrame]


class IngestionHub:
    """Validate -> stamp -> publish. Tracks per-stream counts, rejects and arrival lag."""

    def __init__(self) -> None:
        self._subs: list[Callable[[Frame], None]] = []
        self.counts: Counter[str] = Counter()
        self.rejected = 0
        self.last_lag_s: dict[str, float] = {}

    def subscribe(self, callback: Callable[[Frame], None]) -> None:
        self._subs.append(callback)

    def ingest(self, raw: Frame | dict | str | bytes) -> Frame | None:
        """Accept a model, dict or JSON; returns the validated frame, or None if rejected."""
        try:
            if isinstance(raw, BaseModel):
                frame = raw
            elif isinstance(raw, (str, bytes)):
                frame = _ADAPTER.validate_json(raw)
            else:
                frame = _ADAPTER.validate_python(raw)
        except ValidationError as exc:
            self.rejected += 1
            log.warning("rejected frame: %s", exc.errors()[:1])
            return None
        if not self._plausible(frame):
            self.rejected += 1
            return None
        self.counts[frame.kind] += 1
        self.last_lag_s[frame.kind] = time.time() - frame.t   # only meaningful for wall-clock feeds
        for cb in self._subs:
            cb(frame)
        return frame

    @staticmethod
    def _plausible(f: Frame) -> bool:
        """Cheap sanity gate against corrupt packets."""
        if isinstance(f, MBESFrame):
            n = len(f.beam_angles_deg)
            return n > 0 and len(f.depth_m) == n and len(f.backscatter_db) == n \
                and len(f.water_column_db) == n and len({len(r) for r in f.water_column_db}) == 1
        if isinstance(f, SBPFrame):
            return len(f.trace) > 8 and f.trace_dz_m > 0
        if isinstance(f, Telemetry):
            return -90 <= f.lat <= 90 and -180 <= f.lon <= 180 and f.speed_mps >= 0
        return False

    def stats(self) -> dict:
        return {"counts": dict(self.counts), "rejected": self.rejected}
