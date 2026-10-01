"""Record a full mission from the real backend into frontend/demo/mission.json.gz.

The dashboard replays this file when no backend is reachable (the static GitHub Pages build, or ?demo),
so the demo shows exactly what the live system produces. Usage:  python tools/record_demo.py [decimation]
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.engine import HydroAuraEngine  # noqa: E402


def record(decim: int = 2, out: Path = ROOT / "frontend" / "demo" / "mission.json.gz") -> Path:
    e = HydroAuraEngine()
    hello = e.hello()
    ticks, events, route, anoms, n = [], [], None, None, 0
    while True:
        m = e.tick()
        n += 1
        events += m["events"]
        route = m.get("route", route)            # keep the latest route / anomaly snapshot across skipped ticks
        anoms = m.get("anomalies", anoms)
        done = e.planner.mode == "SURVEY_COMPLETE" or n > 12000
        if n % decim == 0 or done:
            m["events"] = events
            if route is not None:
                m["route"] = route
            if anoms is not None:
                m["anomalies"] = anoms
            ticks.append(m)
            events, route, anoms = [], None, None
        if done:
            break
    doc = {"hello": hello, "ticks": ticks, "dt_sim": e.scenario.survey.tick_dt_s * decim, "decim": decim}
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"recorded {len(ticks)} ticks ({n} engine ticks, {e.sim.sim_t:.0f} sim-s) -> {out} ({out.stat().st_size / 1e6:.2f} MB)")
    return out


if __name__ == "__main__":
    record(int(sys.argv[1]) if len(sys.argv) > 1 else 2)
