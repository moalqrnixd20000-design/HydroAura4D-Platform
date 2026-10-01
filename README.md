# HydroAura 4D — Pipeline & Digital Twin Platform

Closed-loop prototype of an oceanographic command-and-control system: a simulated AUV streams **MBES + SBP + telemetry**
→ **ingestion** → **sensor fusion & edge-AI anomaly engine** → **MOOS-IvP-style adaptive helm** (which steers the vehicle)
→ a live **4D dashboard** (map, 3D digital twin, echograms, HUD).

```
simulator.py ─▶ ingestion.py ─▶ fusion_core.py ─▶ adaptive_planner.py (IvP-style helm) ─┐
   ▲ sensor frames, validated   align + detect      behaviors + priorities              │
   └──────────────────────────── next waypoint ◀─────────────────────────────────────────┘
                              engine.py ─▶ FastAPI WebSocket /ws/stream ─▶ frontend/
```

**Live demo (recorded mission, no backend needed):** the `frontend/` folder is published as a static site that replays a mission
recorded from this backend — see [Static demo](#static-demo-github-pages).

## Run it

Python 3.10+. No Node/npm: Leaflet, Three.js and fonts load from CDNs, so the browser needs internet.

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt

python main.py                    # backend + dashboard  ->  http://localhost:8000
python main.py --split            # backend :8000, frontend served on its own port :5173
python main.py --headless         # no servers: fly the whole mission, print the event log
python -m pytest                  # 14 tests incl. full closed-loop missions (~20 s)
```

`--split` serves `frontend/` with a plain static server; the page finds the backend via `?backend=host:port`
(the launcher prints the URL). If no backend answers, the dashboard falls back to the recorded demo automatically
(`?demo` forces it).

## The dashboard

A full-bleed map / 3D twin with floating glass panels:

| Area | Contents |
|---|---|
| **Left** | System mode (`STANDARD_SURVEY` / `ADAPTIVE_MAPPING` / `SURVEY_COMPLETE`), speed gauge (knots), depth ladder (vehicle · seabed · SBP penetration), backscatter intensity (dB) with sparkline and water-column peak meter, navigation, helm behavior stack, controls |
| **Centre** | **Map** (Esri Dark / Satellite / Ocean basemaps, live hillshaded **bathymetry** or **backscatter** mosaic, planned route, vehicle with sonar ping) · **3D twin** (seabed point cloud, sub-bottom reflectors, plume cone, gas ellipsoid) · **Split** · 4D replay slider |
| **Right** | Live AI anomaly feed: anomaly cards (severity, confidence, status) and the detect / helm event feed |
| **Bottom** | Echogram: MBES water column (absolute depth) above SBP sub-bottom (m below seabed), newest ping at right, with alert / helm flags; plus the cross-track swath fan |

- **Trail colour:** blue = standard survey; yellow → red = adaptive spiral, tightening on the anomaly.
- **Click an anomaly** (card or map pin): the map centres on it and the 3D twin highlights its seabed footprint and flies to it. Click again or press Esc to clear.
- The server keeps a seabed mosaic, so a browser that joins or refreshes mid-mission recovers the map-so-far, trail and log.

## What happens in a run (~2 min at 4×)

1. `BHV_StandardSurvey` flies a lawnmower pattern.
2. ~T+0:26 — MBES water-column returns raise a **gas-plume** candidate, then a high-confidence alert.
3. `BHV_AnomalyMapping` (priority 100) outranks the survey (priority 10): the helm suspends the survey, remembers the
   interruption point, and flies a dense inward spiral at half speed around the anomaly, then hands control back; the survey
   rejoins its line exactly where it left off.
4. ~T+5:00 — the SBP sees a **bright spot with acoustic blanking** under the seabed (shallow gas / hydrocarbon trap):
   second alert, second spiral, survey completes, `BHV_StationKeep` holds.

## The helm (MOOS-IvP inspired)

`backend/adaptive_planner.py` follows the shape of the [MOOS-IvP](https://oceanai.mit.edu/moos-ivp/) helm — behaviors with
priority weights and run conditions reading a shared blackboard, mode switching, and end-flags — without the
interval-programming solver (the highest-priority runnable behavior takes the helm instead of being blended).

| Behavior | Priority | Runs when | Mode |
|---|---|---|---|
| `BHV_AnomalyMapping` | 100 | an `ANOMALY_ALERT` is pending (alerts queue during a spiral) | `ADAPTIVE_MAPPING` |
| `BHV_StandardSurvey` | 10 | survey waypoints remain | `STANDARD_SURVEY` |
| `BHV_StationKeep` | 1 | always (fallback) | `SURVEY_COMPLETE` |

## Layout

| Path | Role |
|---|---|
| `shared/` | `config.py` scenario + hidden truth, `geo.py` ENU↔lat/lon, `schemas.py` Pydantic wire contracts |
| `backend/simulator.py` | Seabed/sub-bottom world model, kinematic AUV, MBES (bathymetry, backscatter, water column), SBP, telemetry |
| `backend/ingestion.py` | Validates dict/JSON/model frames, sanity-gates corrupt packets, pub/sub to fusion |
| `backend/fusion_core.py` | Time alignment (telemetry interpolation, MBES↔SBP pairing), plume + shallow-gas detectors, tracker, severity/confidence |
| `backend/adaptive_planner.py` | The helm: behaviors, blackboard, lawnmower + spiral generators |
| `backend/mosaic.py`, `engine.py`, `app.py` | Seabed mosaic · closed-loop orchestration · FastAPI WebSocket + REST |
| `frontend/` | `index.html`, `style.css`, `app.js`, `map.js`, `twin3d.js`, `echogram.js`, `hud.js`, `demo.js`, `colors.js`, `demo/mission.json.gz` |
| `tools/` | `record_demo.py` (record a mission) · `deploy_pages.py` (publish `frontend/` to the `gh-pages` branch) |

## API

- `WS /ws/stream` — `hello` snapshot (survey, route, anomalies, log, track, seabed mosaic), then `tick` messages: `telemetry`,
  `fused` (swath, water-column fan, SBP trace + reflectors, backscatter metrics), `planner` (mode, behavior stack), `phase`,
  `events`, and `anomalies` / `route` when they change.
- `GET /api/health`, `GET /api/state`, `POST /api/reset`, `POST /api/pause?paused=`, `POST /api/speed/{0.25..16}`
- `POST /api/ingest` — push real sensor frames (`kind`: `telemetry` | `mbes` | `sbp`) into the same pipeline
  (schemas in `shared/schemas.py`, interactive docs at `/docs`).

## How detection works

- **Gas plume (water column):** per ping, mask the mid-water window (15–88 % of range-to-bottom, excluding the bottom
  return), threshold at a *self-calibrating robust baseline* (median/MAD, learned only from clean pings), label connected
  blobs, drop speckle by blob size, geo-reference the blob centroid with the ping's interpolated pose.
- **Shallow gas (sub-bottom):** reflectors are picked from the SBP trace; a *bright spot* and *acoustic blanking*
  (penetration drop) are scored against a running baseline. Absolute depth = MBES nadir seabed + reflector depth.
- **Tracker:** spatial clustering per type; confidence = 1 − exp(−Σscore/2); one alert at confidence ≥ 0.8 and severity ≥ 40.
  Tunables live in `FusionConfig`.

## Static demo (GitHub Pages)

`frontend/` is a plain static site. When it cannot reach a backend (or on `*.github.io`), `demo.js` replays
`frontend/demo/mission.json.gz`, a mission recorded from the real engine — same dashboard, same data, with working
pause / restart / speed. Regenerate and publish:

```bash
python tools/record_demo.py          # re-record the mission
python tools/deploy_pages.py         # force-push frontend/ to the gh-pages branch
```

## Limits of this MVP

- Everything is simulated and tuned to synthetic physics; thresholds need recalibration on real data.
- Anomaly centres are estimated from data seen when the alert fires (typically within ~15 m); spiral radii absorb that error.
  The plume location is its mid-water centroid, not the seabed vent.
- The helm is reactive to alerts only (no obstacle avoidance, currents or endurance planning), and picks one winning behavior
  rather than blending objectives like the real IvP solver.
- Basemap tiles come from Esri's public tile service (attribution shown); over open sea they carry little detail.
