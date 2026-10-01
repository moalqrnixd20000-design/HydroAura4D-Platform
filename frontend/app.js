// HydroAura 4D command & control dashboard: wires data sources to the map, 3D twin, echograms and HUD.
import { TacticalMap } from './map.js';
import { DigitalTwin } from './twin3d.js';
import { Echogram, SwathFan } from './echogram.js';
import { SpeedGauge, DepthLadder, Sparkline } from './hud.js';
import { DemoSource, LiveSource } from './demo.js';
import { ANOM_NAME, COLORS } from './colors.js';

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const params = new URLSearchParams(location.search);
const backend = params.get('backend');
const HTTP = backend ? `http://${backend}` : '';
const WS_URL = `${backend ? 'ws:' : location.protocol === 'https:' ? 'wss:' : 'ws:'}//${backend || location.host}/ws/stream`;

// ---------------------------------------------------------------------------- components
const map = new TacticalMap($('map'), { onSelect: id => select(id) });
let twin = null;                                   // WebGL is optional: the 2D console still works without it
try { twin = new DigitalTwin($('twin')); twin.setActive(false); } catch (e) { console.warn('3D twin unavailable:', e); $('viewSeg').querySelector('[data-view=twin]')?.setAttribute('disabled', ''); }
const echo = new Echogram($('cvEcho'));
const fan = new SwathFan($('cvFan'));
const speedGauge = new SpeedGauge($('speedGauge'));
const depthLadder = new DepthLadder($('depthLadder'));
const spark = new Sparkline($('bsSpark'));

const S = { t0: null, anoms: [], selected: null, view: 'map', paused: false, tick: 0, sea: 120, pen: 25, feedFilter: 'all' };
let source = null;

// ---------------------------------------------------------------------------- layout (panels float over the stage)
function layout() {
  const stage = $('stage'), W = innerWidth;
  const desktop = W >= 1100;
  document.body.classList.toggle('desktop', desktop);
  const L = $('left').getBoundingClientRect(), R = $('right').getBoundingClientRect();
  const D = $('dock').getBoundingClientRect(), T = $('toolbar').getBoundingClientRect(), St = stage.getBoundingClientRect();
  const vis = desktop ? { l: L.right + 10, r: R.left - 10, t: T.bottom + 8, b: D.top - 10 } : { l: St.left, r: St.right, t: St.top, b: St.bottom };
  const split = Math.round(vis.l + (vis.r - vis.l) / 2);
  stage.style.setProperty('--split', `${split}px`);
  for (const [el, id] of [[$('map'), 'map'], [$('twin'), 'twin']]) {
    const c = el.getBoundingClientRect();
    if (!c.width) continue;
    const l = Math.max(0, vis.l - c.left), r = Math.max(0, c.right - vis.r), t = Math.max(0, vis.t - c.top), b = Math.max(0, c.bottom - vis.b);
    el.style.setProperty('--ins-l', `${l}px`); el.style.setProperty('--ins-r', `${r}px`);
    el.style.setProperty('--ins-t', `${t}px`); el.style.setProperty('--ins-b', `${b}px`);
    if (id === 'map') map.setPadding([l + 16, t + 16], [r + 16, b + 16]);
    else if (twin) twin.setViewOffset((l - r) / 2, (t - b) / 2);
  }
  map.resize();
}
new ResizeObserver(layout).observe(document.body);
addEventListener('resize', layout);

function setView(v) {
  if (v === 'twin' && !twin) return;
  S.view = v;
  const stage = $('stage');
  stage.className = `stage ${v}`;
  $('map').classList.toggle('active', v !== 'twin');
  $('twin').classList.toggle('active', v !== 'map');
  twin && twin.setActive(v !== 'map');
  $('timeline').hidden = v === 'map';
  document.querySelectorAll('#viewSeg button').forEach(b => b.classList.toggle('on', b.dataset.view === v));
  requestAnimationFrame(() => { layout(); if (v !== 'twin') map.resize(); });
}

// ---------------------------------------------------------------------------- formatting
const fmtClock = s => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const fmtT = t => (S.t0 == null ? '' : fmtClock(Math.max(0, t - S.t0)));

// ---------------------------------------------------------------------------- link badge
function setLink(kind) {
  const b = $('link');
  const [txt, cls] = { live: ['LIVE LINK', 'on'], demo: ['DEMO REPLAY', 'demo'], 'demo-done': ['REPLAY COMPLETE', 'demo'], connecting: ['CONNECTING', 'demo'],
    loading: ['LOADING DEMO', 'demo'], offline: ['OFFLINE', 'off'] }[kind] || ['OFFLINE', 'off'];
  b.textContent = txt; b.className = `badge ${cls}`;
  if (kind === 'demo-done') flash('Replay complete. Use Restart mission to run it again.', 'info');
}

let bannerTimer;
function flash(msg, level = 'alert') {
  const b = $('banner'); b.textContent = msg; b.className = `banner ${level}`; b.hidden = false;
  clearTimeout(bannerTimer); bannerTimer = setTimeout(() => (b.hidden = true), 7000);
}

// ---------------------------------------------------------------------------- feed + anomalies
const TAG = { detection: 'DETECT', routing: 'HELM', system: 'SYS' };
function addFeed(ev, silent) {
  const li = document.createElement('li');
  li.className = `${ev.level} ${ev.category}`;
  li.innerHTML = `<time>${esc(fmtT(ev.t))}</time><b>${TAG[ev.category] || 'SYS'}</b><span>${esc(ev.message)}</span>`;
  const feed = $('feed'); feed.prepend(li);
  while (feed.children.length > 150) feed.lastChild.remove();
  if (silent) return;
  if (ev.level === 'alert') { flash(ev.message, 'alert'); echo.mark(`ALERT ${ev.anomaly_id || ''}`.trim(), COLORS.red); }
  else if (ev.category === 'routing' && ev.level === 'warning') echo.mark('HELM → MAPPING', COLORS.amber);
  else if (ev.category === 'routing' && ev.level === 'success' && ev.anomaly_id) echo.mark(`MAPPED ${ev.anomaly_id}`, COLORS.green);
}

function renderAnoms(list) {
  S.anoms = list;
  $('anomCount').textContent = list.length;
  const box = $('anoms');
  if (!list.length) { box.innerHTML = '<p class="empty">No anomalies yet. The detector learns the noise floor in the first few seconds of survey.</p>'; return; }
  box.innerHTML = list.map(a => `
    <button class="anom ${esc(a.type)}${a.id === S.selected ? ' sel' : ''}" data-id="${esc(a.id)}" aria-pressed="${a.id === S.selected}">
      <span class="a-top"><b>${esc(a.id)}</b><em>${esc(ANOM_NAME[a.type] || a.type)}</em><i class="chip ${esc(a.status)}">${esc(a.status)}</i></span>
      <span class="a-meta">${a.lat.toFixed(5)}° ${a.lon.toFixed(5)}° · ${a.depth_m.toFixed(0)} m</span>
      <span class="a-bars">
        <span title="Severity ${a.severity.toFixed(0)}/100"><small>SEV</small><u><s style="width:${Math.min(100, a.severity)}%"></s></u><b>${a.severity.toFixed(0)}</b></span>
        <span title="Confidence ${(a.confidence * 100).toFixed(0)}%"><small>CONF</small><u class="cf"><s style="width:${a.confidence * 100}%"></s></u><b>${(a.confidence * 100).toFixed(0)}%</b></span>
      </span>
    </button>`).join('');
}
$('anoms').addEventListener('click', e => { const el = e.target.closest('.anom'); if (el) select(el.dataset.id); });

/** Selecting an anomaly centres the map on it and highlights its footprint in the 3D twin. */
function select(id) {
  if (id === S.selected) id = null;                    // click again to clear
  S.selected = id;
  const a = S.anoms.find(x => x.id === id);
  map.select(id);
  if (a) { map.focus(a); twin && twin.highlight(id, true); }
  else { twin && (twin.clearHighlight(), twin.resetView()); }
  renderAnoms(S.anoms);
}
addEventListener('keydown', e => { if (e.key === 'Escape' && S.selected) select(S.selected); });

// ---------------------------------------------------------------------------- HUD
function renderPlanner(p) {
  const card = $('modeCard');
  card.dataset.mode = p.mode;
  $('modeName').textContent = p.mode;
  $('modeSub').textContent = p.mode === 'ADAPTIVE_MAPPING' ? `BHV_AnomalyMapping · ${p.active_anomaly || ''} · spiral ${(p.map_progress * 100).toFixed(0)}%${p.queued.length ? ` · ${p.queued.length} queued` : ''}`
    : p.mode === 'SURVEY_COMPLETE' ? 'BHV_StationKeep · holding position' : 'BHV_StandardSurvey · lawnmower';
  $('progress').style.width = `${(p.survey_progress * 100).toFixed(0)}%`;
  $('progTxt').textContent = `${(p.survey_progress * 100).toFixed(0)}% survey lines`;
  $('spiralTxt').textContent = `${p.spirals_done} anomal${p.spirals_done === 1 ? 'y' : 'ies'} mapped`;
  $('bhv').innerHTML = (p.behaviors || []).map(b => `<li class="${esc(b.state)}"><span class="pr">${b.priority}</span><b>${esc(b.name)}</b><em>${esc(b.state)}</em><small>${esc(b.detail)}</small></li>`).join('');
}

function renderTelemetry(t, fused) {
  speedGauge.set(t.speed_mps);
  $('nLat').textContent = `${t.lat.toFixed(5)}°`; $('nLon').textContent = `${t.lon.toFixed(5)}°`;
  $('nHdg').textContent = `${t.heading_deg.toFixed(0)}°`;
  if (fused.sbp) { S.sea = fused.sbp.seabed_m; S.pen = fused.sbp.penetration_m; $('nSea').textContent = `${S.sea.toFixed(1)} m`; }
  depthLadder.set(t.depth_m, S.sea, S.pen);
  const m = fused.metrics;
  if (m) {
    $('bsVal').textContent = m.bs_nadir_db.toFixed(1); spark.push(m.bs_nadir_db);
    if (m.wc_peak_db != null && m.wc_floor_db != null) {
      const ex = m.wc_peak_db - m.wc_floor_db, f = Math.max(0, Math.min(1, ex / 35));
      $('wcTxt').textContent = `${m.wc_peak_db.toFixed(0)} dB · +${Math.max(0, ex).toFixed(0)} over floor`;
      const bar = $('wcBar'); bar.style.width = `${(f * 100).toFixed(0)}%`; bar.dataset.lvl = f > .6 ? 'hi' : f > .3 ? 'mid' : 'lo';
    }
  }
}

// ---------------------------------------------------------------------------- data handlers
function onHello(h) {
  S.t0 = h.events.length ? h.events[0].t : null; S.tick = 0; S.selected = null;
  layout();                                          // measure the floating panels before the first map fit
  map.init(h);
  $('feed').innerHTML = ''; h.events.forEach(e => addFeed(e, true));
  renderAnoms(h.anomalies); renderPlanner(h.planner);
  echo.reset(); spark.reset();
  if (twin) {
    twin.reset(); twin.backfill(h.mosaic); twin.setRoute(h.route); twin.setAnomalies(h.anomalies); twin.setTruth(h.truth);
    for (const p of h.track || []) twin.addTrack({ east_m: p[0], north_m: p[1], depth_m: 15 }, p[3] || 0);
  }
  S.paused = !!h.paused; $('btnPause').textContent = S.paused ? 'Resume' : 'Pause';
  layout();
}

function onTick(m) {
  if (S.t0 == null) S.t0 = m.t - m.sim_s;
  S.tick++;
  const phase = m.phase || 0;
  $('clock').textContent = fmtClock(m.sim_s);
  m.events.forEach(e => addFeed(e));
  if (m.route) { map.setRoute(m.route); twin && twin.setRoute(m.route); }
  if (m.anomalies) { renderAnoms(m.anomalies); map.setAnomalies(m.anomalies); twin && twin.setAnomalies(m.anomalies); }
  renderPlanner(m.planner); renderTelemetry(m.telemetry, m.fused);
  if (m.fused.swath) map.addSoundings(m.fused.swath);
  map.updateVehicle(m.telemetry, m.fused.swath, phase);
  echo.push(m.fused); fan.draw(m.fused);
  twin && twin.addTick(m.t, m.telemetry, m.fused, phase);
}

// ---------------------------------------------------------------------------- controls
$('btnPause').onclick = async () => { S.paused = !S.paused; $('btnPause').textContent = S.paused ? 'Resume' : 'Pause'; await source?.pause(S.paused); };
$('btnReset').onclick = async () => { S.paused = false; $('btnPause').textContent = 'Pause'; await source?.reset(); };
$('chkTruth').onchange = e => { map.showTruth(e.target.checked); twin && twin.showTruth(e.target.checked); };
const seg = (id, attr, fn) => $(id).addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b || b.disabled) return;
  $(id).querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b)); fn(b.dataset[attr]);
});
seg('speedSeg', 'speed', v => source?.setSpeed(+v));
seg('viewSeg', 'view', v => setView(v));
seg('baseSeg', 'base', v => map.setBasemap(v));
seg('ovSeg', 'ov', v => map.setOverlay(v));
seg('feedSeg', 'f', v => { $('feed').dataset.f = v; });

// 4D replay slider (3D twin): drag back through the mission, LIVE returns to the stream
let scrubbing = false;
const slider = $('timeSlider'), liveBtn = $('btnLive');
const setScrub = on => { scrubbing = on; liveBtn.classList.toggle('replay', on); liveBtn.textContent = on ? 'GO LIVE' : 'LIVE'; };
slider.oninput = () => {
  if (!twin) return;
  const f = slider.value / 1000;
  if (f >= .999) { setScrub(false); twin.setLive(true); return; }
  setScrub(true); twin.scrub(f);
};
liveBtn.onclick = () => { slider.value = 1000; setScrub(false); twin?.setLive(true); };
setInterval(() => { if (!scrubbing) slider.value = 1000; }, 500);

// ---------------------------------------------------------------------------- boot
async function startDemo() {
  const demo = new DemoSource({ status: setLink, hello: onHello, tick: onTick });
  try { await demo.start(); source = demo; }
  catch (err) { setLink('offline'); flash(`No backend reachable and no recorded mission found (${err.message}). Start the backend with: python main.py`, 'alert'); }
}

async function boot() {
  setView('map');
  const staticHost = /\.github\.io$/.test(location.hostname) || location.protocol === 'file:';
  if (params.has('demo') || staticHost) return startDemo();
  const live = new LiveSource({ status: setLink, hello: onHello, tick: onTick }, { http: HTTP, wsUrl: WS_URL });
  if (await live.start()) { source = live; return; }
  live.stop();
  return startDemo();
}
boot();

window.__ha = { map, twin, S };   // debug handle (console inspection)
