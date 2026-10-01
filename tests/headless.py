"""Headless closed-loop run (no sleeping): prints the event log. Usage: python -m tests.headless"""
import time
from backend.engine import HydroAuraEngine

def run(engine=None, max_ticks=6000, verbose=True):
    e = engine or HydroAuraEngine()
    t0 = time.time()
    for i in range(max_ticks):
        msg = e.tick()
        for ev in msg["events"]:
            if verbose:
                print(f"[{msg['sim_s']:7.1f}s] {ev['level']:7s} {ev['category']:9s} {ev['message']}")
        if e.planner.mode == "SURVEY_COMPLETE":
            break
    if verbose:
        print(f"done in {e.sim.sim_t:.0f} sim-s, {time.time()-t0:.1f} wall-s, ticks={e.tick_no}")
    return e

if __name__ == "__main__":
    run()
