"""End-to-end closed-loop tests: simulator -> ingestion -> fusion -> planner."""
import math
from dataclasses import replace

import pytest

from backend.engine import HydroAuraEngine
from shared.config import Scenario
from tests.headless import run


@pytest.fixture(scope="module")
def mission():
    e = run(verbose=False)
    return e


def test_mission_completes_with_two_adaptive_spirals(mission):
    assert mission.planner.mode == "SURVEY_COMPLETE"
    assert mission.planner.status()["spirals_done"] == 2
    assert mission.ingest.rejected == 0


def test_both_anomaly_types_found_close_to_truth(mission):
    found = {a.type: a for a in mission.fusion.tracker.snapshot()}
    assert set(found) == {"gas_plume", "shallow_gas"}
    truth = mission.scenario
    p, k = found["gas_plume"], found["shallow_gas"]
    assert math.hypot(p.east_m - truth.plume.x, p.north_m - truth.plume.y) < 30
    assert math.hypot(k.east_m - truth.pocket.x, k.north_m - truth.pocket.y) < 30
    assert all(a.status == "surveyed" and a.confidence >= 0.8 for a in found.values())
    # shallow gas depth = seabed (~124 m here) + ~15 m below seabed
    assert 130 < k.depth_m < 150


def test_event_log_tells_the_story_in_order(mission):
    msgs = [e["message"] for e in mission.events]
    first_alert = next(i for i, m in enumerate(msgs) if "HIGH-CONFIDENCE" in m)
    reroute = next(i for i, m in enumerate(msgs) if "Adaptive re-route" in m)
    assert reroute == first_alert + 1


def test_no_false_alarms_on_a_clean_seabed():
    clean = replace(Scenario(), plume=None, pocket=None)
    e = run(HydroAuraEngine(clean), verbose=False)
    assert e.planner.mode == "SURVEY_COMPLETE"
    assert e.planner.status()["spirals_done"] == 0
    assert e.fusion.tracker.snapshot() == []
