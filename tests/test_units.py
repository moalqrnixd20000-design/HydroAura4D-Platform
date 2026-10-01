import math

import numpy as np
import pytest

from backend.adaptive_planner import (ADAPTIVE_MAPPING, STANDARD_SURVEY, SURVEY_COMPLETE, AdaptivePlanner,
                                       lawnmower, spiral)
from backend.fusion_core import TrackBuffer
from backend.ingestion import IngestionHub
from shared.config import SurveyConfig
from shared.geo import geo_to_local, local_to_geo, wrap_deg
from shared.schemas import Anomaly, Telemetry


def test_geo_roundtrip():
    lat, lon = local_to_geo(123.0, -456.0)
    x, y = geo_to_local(lat, lon)
    assert (float(x), float(y)) == pytest.approx((123.0, -456.0), abs=1e-6)


def test_wrap_deg():
    assert wrap_deg(190) == pytest.approx(-170)
    assert wrap_deg(-180) == 180


def test_lawnmower_covers_block_in_alternating_lines():
    wps = lawnmower(SurveyConfig())
    assert len(wps) == 8
    assert [w.y for w in wps[::2]] == [50, 150, 250, 350]
    assert wps[0].x < wps[1].x and wps[2].x > wps[3].x      # boustrophedon


def test_spiral_shrinks_monotonically_from_entry_bearing():
    cx, cy = 100.0, 100.0
    wps = spiral(cx, cy, 70, 20, 15, entry_bearing_deg=270, speed=6)
    r = [math.hypot(w.x - cx, w.y - cy) for w in wps]
    assert r[0] == pytest.approx(70, abs=0.5)
    assert wps[0].x < cx                                    # bearing 270 = west of the centre
    assert all(b <= a + 1e-6 for a, b in zip(r, r[1:]))
    assert r[-1] < 25


def _anomaly(x=200.0, y=100.0, kind="gas_plume"):
    lat, lon = local_to_geo(x, y)
    return Anomaly(id="ANOM-001", type=kind, lat=float(lat), lon=float(lon), east_m=x, north_m=y, depth_m=80,
                   severity=80, confidence=0.9, hits=5, first_seen=0, last_seen=0)


def _fly(p, until_mode):
    """Drive the helm by 'teleporting' the vehicle onto whatever waypoint it currently wants."""
    events, guard = [], 0
    while p.mode != until_mode and guard < 2000:
        w = p.current_target()
        events += p.update(w.x, w.y)
        guard += 1
    return events


def test_helm_starts_in_standard_survey_with_priority_stack():
    p = AdaptivePlanner(SurveyConfig())
    st = p.status()
    assert p.mode == STANDARD_SURVEY and st["active_behavior"] == "BHV_StandardSurvey"
    assert [b["name"] for b in st["behaviors"]] == ["BHV_AnomalyMapping", "BHV_StandardSurvey", "BHV_StationKeep"]
    assert [b["priority"] for b in st["behaviors"]] == [100, 10, 1]


def test_alert_preempts_survey_then_resumes_exactly_where_it_left():
    p = AdaptivePlanner(SurveyConfig())
    before = len(p.survey.plan())
    ev = p.handle_alert(_anomaly(), 150.0, 50.0)
    assert p.mode == ADAPTIVE_MAPPING and p.current_target().kind == "spiral"
    assert p.survey.state == "suspended" and "outranks" in ev[0].message
    # while mapping, the planned route is: spiral ... then rejoin point, then the rest of the survey
    kinds = [w.kind for w in p.route]
    assert kinds[0] == "spiral" and "rejoin" in kinds
    rj = next(w for w in p.route if w.kind == "rejoin")
    assert (rj.x, rj.y) == (150.0, 50.0)

    events = _fly(p, STANDARD_SURVEY)
    assert any(e.kind == "mapping_complete" and e.done_anomaly == "ANOM-001" for e in events)
    assert p.current_target().kind == "rejoin"                  # first stop: the interruption point
    assert p.survey.state == "running" and p.status()["spirals_done"] == 1
    assert len(p.survey.plan()) == before + 1                   # whole survey preserved (+ the rejoin leg)


def test_helm_completes_survey_and_station_keeps():
    p = AdaptivePlanner(SurveyConfig())
    _fly(p, SURVEY_COMPLETE)
    assert p.current_target() is None and p.status()["active_behavior"] == "BHV_StationKeep"
    assert p.bb.survey_complete and p.status()["survey_progress"] == 1.0


def test_helm_ignores_duplicate_alert_and_queues_concurrent_one():
    p = AdaptivePlanner(SurveyConfig())
    a = _anomaly()
    p.handle_alert(a, 0, 50)
    assert p.handle_alert(a, 0, 50) == []
    b = _anomaly(400, 250, "shallow_gas").model_copy(update={"id": "ANOM-002"})
    ev = p.handle_alert(b, 0, 50)
    assert "queued" in ev[0].message and p.status()["queued"] == ["ANOM-002"]
    events = _fly(p, STANDARD_SURVEY)                             # both spirals flown back-to-back
    assert [e.done_anomaly for e in events if e.kind == "mapping_complete"] == ["ANOM-001", "ANOM-002"]


def test_ingestion_validates_and_rejects():
    hub = IngestionHub()
    got = []
    hub.subscribe(got.append)
    good = dict(kind="telemetry", t=1.0, lat=27, lon=34, east_m=0, north_m=0, depth_m=10, heading_deg=0, speed_mps=1)
    assert hub.ingest(good) is not None
    assert hub.ingest('{"kind":"telemetry","t":2,"lat":27,"lon":34,"east_m":0,"north_m":0,"depth_m":1,'
                      '"heading_deg":0,"speed_mps":1}') is not None
    assert hub.ingest({"kind": "telemetry", "t": "bad"}) is None
    assert hub.ingest({"kind": "nope"}) is None
    bad_mbes = dict(kind="mbes", t=1, ping_id=1, beam_angles_deg=[0, 1], depth_m=[10], backscatter_db=[1, 2],
                    wc_bin_m=1.0, water_column_db=[[1], [1]])
    assert hub.ingest(bad_mbes) is None                      # beam-count mismatch
    assert len(got) == 2 and hub.rejected == 3


def test_track_buffer_interpolates_and_limits_extrapolation():
    tb = TrackBuffer()
    for t, x in ((0.0, 0.0), (1.0, 10.0)):
        tb.add(Telemetry(t=t, lat=0, lon=0, east_m=x, north_m=0, depth_m=15, heading_deg=90, speed_mps=10))
    assert tb.pose_at(0.5, 0.25).x == pytest.approx(5.0)
    assert tb.pose_at(1.1, 0.25).x == pytest.approx(11.0)    # dead-reckoned east at 10 m/s
    assert tb.pose_at(1.5, 0.25) is None                     # too far past the last fix -> wait
    assert tb.pose_at(-1.0, 0.25) is None
