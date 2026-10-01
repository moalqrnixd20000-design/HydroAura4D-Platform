// 3D digital twin (Three.js): live seabed point cloud, sub-bottom reflectors, colour-coded trail, vehicle,
// anomalies with selectable footprint, 4D time scrubbing.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { DEPTH_RAMP, trailBucket, trailColor, ANOM } from './colors.js';

const VEX = 1.5;                       // vertical exaggeration
const MAX_PTS = 300000;
const CX = 300, CY = 200;              // scene centre (east, north) in survey metres
const toScene = (e, n, depth) => new THREE.Vector3(e - CX, -depth * VEX, -(n - CY));

class PointStore {
  constructor(scene, size, max = MAX_PTS) {
    this.max = max; this.count = 0;
    this.pos = new Float32Array(max * 3); this.col = new Float32Array(max * 3);
    const g = new THREE.BufferGeometry();
    this.posAttr = new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage);
    this.colAttr = new THREE.BufferAttribute(this.col, 3).setUsage(THREE.DynamicDrawUsage);
    g.setAttribute('position', this.posAttr); g.setAttribute('color', this.colAttr); g.setDrawRange(0, 0);
    this.geo = g;
    this.points = new THREE.Points(g, new THREE.PointsMaterial({ size, vertexColors: true, sizeAttenuation: true, transparent: true, opacity: .95 }));
    this.points.frustumCulled = false; scene.add(this.points);
  }
  add(v, r, g, b) {
    if (this.count >= this.max) return;
    const i = this.count * 3;
    this.pos[i] = v.x; this.pos[i + 1] = v.y; this.pos[i + 2] = v.z; this.col[i] = r; this.col[i + 1] = g; this.col[i + 2] = b;
    this.count++;
  }
  flush() { this.posAttr.needsUpdate = true; this.colAttr.needsUpdate = true; this.geo.setDrawRange(0, this.count); }
  setVisible(n) { this.geo.setDrawRange(0, Math.min(n, this.count)); }
  reset() { this.count = 0; this.geo.setDrawRange(0, 0); }
}

function labelSprite(text, color) {
  const cv = document.createElement('canvas'); cv.width = 256; cv.height = 64;
  const g = cv.getContext('2d');
  g.fillStyle = 'rgba(10,15,29,.82)'; g.fillRect(0, 8, 256, 48); g.strokeStyle = color; g.lineWidth = 2; g.strokeRect(1, 9, 254, 46);
  g.font = '600 26px "Chakra Petch", system-ui, sans-serif'; g.fillStyle = color; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(text, 128, 33);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(cv), depthTest: false, transparent: true }));
  s.scale.set(64, 16, 1); s.renderOrder = 10; return s;
}

export class DigitalTwin {
  constructor(el) {
    this.el = el;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    el.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0x0a0f1d, .0007);
    this.camera = new THREE.PerspectiveCamera(48, 1, 1, 7000);
    this.camera.position.set(-380, 300, 520);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(0, -170, 0); this.controls.enableDamping = true; this.controls.maxPolarAngle = Math.PI * .92;
    this.scene.add(new THREE.AmbientLight(0xffffff, .9));
    this._buildStatic();

    this.seabed = new PointStore(this.scene, 3.4);
    this.layers = new PointStore(this.scene, 3.0, 120000);
    this.tickEnds = []; this.base = { seabed: 0, layers: 0 };
    this._initTrack();
    this.routeLine = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0xffd400, transparent: true, opacity: .75 }));
    this.scene.add(this.routeLine);
    this.vehicle = this._buildVehicle(); this.scene.add(this.vehicle);

    this.anomalies = new Map();
    this.hl = null; this.selectedId = null;
    this.truth = new THREE.Group(); this.truth.visible = false; this.scene.add(this.truth);
    this.grid = new Map();
    this.live = true; this.offset = [0, 0]; this._fly = null; this.active = true;
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(el);
    this.resize();
    this.renderer.setAnimationLoop(() => this._frame());
  }

  _buildStatic() {
    const surf = new THREE.Mesh(new THREE.PlaneGeometry(900, 700), new THREE.MeshBasicMaterial({ color: 0x00f3ff, transparent: true, opacity: .05, side: THREE.DoubleSide }));
    surf.rotation.x = -Math.PI / 2; this.scene.add(surf);
    const grid = new THREE.GridHelper(900, 18, 0x00f3ff, 0x123152); grid.material.transparent = true; grid.material.opacity = .4; this.scene.add(grid);
    const o = [[0, 0], [600, 0], [600, 400], [0, 400], [0, 0]].map(([e, n]) => toScene(e, n, 0));
    this.scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(o), new THREE.LineBasicMaterial({ color: 0x00f3ff })));
    const floor = o.map(v => new THREE.Vector3(v.x, -175 * VEX, v.z));
    this.scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(floor), new THREE.LineBasicMaterial({ color: 0x1a3a5c })));
    for (const v of o.slice(0, 4)) this.scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([v, new THREE.Vector3(v.x, -175 * VEX, v.z)]),
      new THREE.LineBasicMaterial({ color: 0x123152, transparent: true, opacity: .6 })));
  }

  _initTrack() {
    const max = 6000, g = new THREE.BufferGeometry();
    this.trackPos = new THREE.BufferAttribute(new Float32Array(max * 3), 3); this.trackCol = new THREE.BufferAttribute(new Float32Array(max * 3), 3);
    g.setAttribute('position', this.trackPos); g.setAttribute('color', this.trackCol); g.setDrawRange(0, 0);
    this.trackLine = new THREE.Line(g, new THREE.LineBasicMaterial({ vertexColors: true })); this.trackLine.frustumCulled = false;
    this.scene.add(this.trackLine); this.trackN = 0; this.trackMax = max;
  }

  _buildVehicle() {
    const g = new THREE.Group();
    const body = new THREE.Mesh(new THREE.CylinderGeometry(3, 3, 22, 12), new THREE.MeshBasicMaterial({ color: 0x00f3ff }));
    body.rotation.z = Math.PI / 2;
    const nose = new THREE.Mesh(new THREE.ConeGeometry(3, 7, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }));
    nose.rotation.z = -Math.PI / 2; nose.position.x = 14.5;
    g.add(body, nose);
    this.plumb = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 100, 0)]),
      new THREE.LineBasicMaterial({ color: 0x00f3ff, transparent: true, opacity: .35 }));
    g.add(this.plumb); g.scale.setScalar(1.6); return g;
  }

  // ---- data in -----------------------------------------------------------------------------
  reset() {
    this.seabed.reset(); this.layers.reset(); this.tickEnds = []; this.base = { seabed: 0, layers: 0 };
    this.trackN = 0; this.trackLine.geometry.setDrawRange(0, 0); this.grid.clear();
    for (const a of this.anomalies.values()) this.scene.remove(a.group);
    this.anomalies.clear(); this.clearHighlight(); this.setRoute([]); this.live = true;
  }

  /** Rebuild the seabed cloud from the server mosaic so a late joiner sees the map-so-far. */
  backfill(m) {
    if (!m || !m.depth_b64) return;
    const dq = Uint8Array.from(atob(m.depth_b64), c => c.charCodeAt(0)), c = new THREE.Color();
    for (let i = 0; i < dq.length; i++) {
      if (!dq[i]) continue;
      const x = m.x0 + ((i % m.nx) + .5) * m.cell, y = m.y0 + (Math.floor(i / m.nx) + .5) * m.cell;
      const d = m.dmin + (dq[i] - 1) / 254 * (m.dmax - m.dmin), [r, g, b] = DEPTH_RAMP((d - m.dmin) / (m.dmax - m.dmin));
      this.seabed.add(toScene(x, y, d), r / 255, g / 255, b / 255);
      this.grid.set(Math.round(x / 10) + ',' + Math.round(y / 10), d);
    }
    this.seabed.flush(); this.base.seabed = this.seabed.count;
  }

  setRoute(route) {
    const pts = route.map(w => toScene(w.east_m, w.north_m, 12));
    this.routeLine.geometry.dispose(); this.routeLine.geometry = new THREE.BufferGeometry().setFromPoints(pts);
  }

  addTrack(tel, phase) {
    if (this.trackN >= this.trackMax) return;
    const v = toScene(tel.east_m, tel.north_m, tel.depth_m), c = new THREE.Color(trailColor(trailBucket(phase)));
    this.trackPos.setXYZ(this.trackN, v.x, v.y, v.z); this.trackCol.setXYZ(this.trackN, c.r, c.g, c.b); this.trackN++;
    this.trackPos.needsUpdate = this.trackCol.needsUpdate = true; this.trackLine.geometry.setDrawRange(0, this.trackN);
  }

  addTick(t, tel, fused, phase = 0) {
    const sw = fused.swath;
    if (sw) {
      for (let i = 0; i < sw.east_m.length; i++) {
        const [r, g, b] = DEPTH_RAMP((sw.depth_m[i] - 100) / 40);
        this.seabed.add(toScene(sw.east_m[i], sw.north_m[i], sw.depth_m[i]), r / 255, g / 255, b / 255);
        this.grid.set(Math.round(sw.east_m[i] / 10) + ',' + Math.round(sw.north_m[i] / 10), sw.depth_m[i]);
      }
    }
    if (fused.sbp) {
      const s = fused.sbp;
      for (const r of s.reflectors) {
        const hot = r.amp > 1.5;
        this.layers.add(toScene(s.east_m, s.north_m, r.depth_m), 1, hot ? .2 : .83, hot ? .85 : 0);
      }
    }
    this.seabed.flush(); this.layers.flush();
    this.addTrack(tel, phase);
    this.tickEnds.push({ t, seabed: this.seabed.count, layers: this.layers.count, trk: this.trackN, p: [tel.east_m, tel.north_m, tel.depth_m, tel.heading_deg] });
    if (this.live) this._placeVehicle(...this.tickEnds.at(-1).p);
  }

  _placeVehicle(e, n, d, hdg) {
    this.vehicle.position.copy(toScene(e, n, d));
    this.vehicle.rotation.y = (90 - hdg) * Math.PI / 180;
    this.plumb.scale.y = Math.max(.1, (d * VEX) / 160);
  }

  seabedAt(e, n, fallback = 125) {
    for (let r = 0; r < 5; r++) for (let de = -r; de <= r; de++) for (let dn = -r; dn <= r; dn++) {
      const v = this.grid.get((Math.round(e / 10) + de) + ',' + (Math.round(n / 10) + dn)); if (v) return v;
    }
    return fallback;
  }

  // ---- anomalies ---------------------------------------------------------------------------
  setAnomalies(list) {
    for (const a of list) {
      let rec = this.anomalies.get(a.id);
      if (!rec) {
        const g = new THREE.Group(), col = new THREE.Color(ANOM[a.type]), sb = this.seabedAt(a.east_m, a.north_m);
        if (a.type === 'gas_plume') {
          const h = Math.min(75, sb - 14);
          const cone = new THREE.Mesh(new THREE.CylinderGeometry(14, 5, h * VEX, 20, 1, true),
            new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: .3, side: THREE.DoubleSide, depthWrite: false }));
          cone.position.y = -(sb - h / 2) * VEX; cone.userData.pulse = true; g.add(cone);
          const core = new THREE.Mesh(new THREE.SphereGeometry(4, 12, 12), new THREE.MeshBasicMaterial({ color: 0xffb3c4 }));
          core.position.y = -a.depth_m * VEX; g.add(core);
        } else {
          const e = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 16), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: .38, depthWrite: false }));
          e.scale.set(a.radius_m * .8, 5 * VEX, a.radius_m * .8); e.position.y = -a.depth_m * VEX; e.userData.pulse = true; g.add(e);
          g.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, -a.depth_m * VEX, 0), new THREE.Vector3(0, -sb * VEX, 0)]),
            new THREE.LineBasicMaterial({ color: col })));
        }
        const lab = labelSprite(a.id, ANOM[a.type]); lab.position.y = -(a.type === 'gas_plume' ? 12 : sb - 20) * VEX; g.add(lab);
        g.userData = { sb };
        this.scene.add(g); rec = { group: g, first_seen: a.first_seen, a }; this.anomalies.set(a.id, rec);
      }
      rec.a = a; rec.first_seen = a.first_seen;
      rec.group.position.set(a.east_m - CX, 0, -(a.north_m - CY));
    }
    if (this.selectedId) this.highlight(this.selectedId, false);
  }

  /** Highlight an anomaly's footprint on the seabed (ring + beam) and optionally fly the camera to it. */
  highlight(id, fly = true) {
    this.clearHighlight();
    const rec = this.anomalies.get(id); if (!rec) return;
    this.selectedId = id;
    const a = rec.a, sb = rec.group.userData.sb, y = -sb * VEX + .6, col = new THREE.Color(ANOM[a.type]);
    const g = new THREE.Group(); g.position.copy(rec.group.position);
    const ring = new THREE.Mesh(new THREE.RingGeometry(a.radius_m * .93, a.radius_m, 72), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: .95, side: THREE.DoubleSide, depthTest: false }));
    ring.rotation.x = -Math.PI / 2; ring.position.y = y; ring.renderOrder = 5;
    const disc = new THREE.Mesh(new THREE.CircleGeometry(a.radius_m * .93, 72), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: .22, side: THREE.DoubleSide, depthTest: false }));
    disc.rotation.x = -Math.PI / 2; disc.position.y = y; disc.renderOrder = 4;
    const beam = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, y, 0), new THREE.Vector3(0, 0, 0)]), new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: .6 }));
    g.add(disc, ring, beam); g.userData.ring = ring;
    this.scene.add(g); this.hl = g;
    if (fly) this.flyTo(rec.group.position.x, -((a.type === 'gas_plume' ? a.depth_m : sb) * VEX), rec.group.position.z, Math.max(330, a.radius_m * 7));
  }
  clearHighlight() { if (this.hl) { this.scene.remove(this.hl); this.hl = null; } this.selectedId = null; }
  resetView() { this._fly = { t0: performance.now(), dur: 900, p0: this.camera.position.clone(), t0v: this.controls.target.clone(), p1: new THREE.Vector3(-380, 300, 520), t1: new THREE.Vector3(0, -170, 0) }; }

  flyTo(x, y, z, dist) {
    const tgt = new THREE.Vector3(x, y, z), dir = this.camera.position.clone().sub(this.controls.target).normalize();
    if (dir.y < .25) dir.y = .45;                                  // look down onto the footprint
    dir.normalize();
    this._fly = { t0: performance.now(), dur: 900, p0: this.camera.position.clone(), t0v: this.controls.target.clone(), p1: tgt.clone().addScaledVector(dir, dist), t1: tgt };
  }

  setTruth(truth) {
    this.truth.clear();
    if (!truth) return;
    const mat = new THREE.LineBasicMaterial({ color: 0xffffff });
    if (truth.plume) { const p = truth.plume; this.truth.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([toScene(p.east_m, p.north_m, p.seabed_m), toScene(p.east_m, p.north_m, p.seabed_m - p.height_m)]), mat)); }
    if (truth.pocket) { const k = truth.pocket, m = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true })); m.scale.set(k.radius_m, 6, k.radius_m); m.position.copy(toScene(k.east_m, k.north_m, k.seabed_m + k.depth_bsb_m)); this.truth.add(m); }
  }
  showTruth(on) { this.truth.visible = on; }

  // ---- 4D time control -----------------------------------------------------------------------
  setLive(on) {
    this.live = on;
    if (on) {
      this.seabed.setVisible(Infinity); this.layers.setVisible(Infinity); this.trackLine.geometry.setDrawRange(0, this.trackN);
      this._showAnoms(Infinity); if (this.tickEnds.length) this._placeVehicle(...this.tickEnds.at(-1).p);
    }
  }
  scrub(frac) {
    this.live = false;
    const n = this.tickEnds.length; if (!n) return;
    const te = this.tickEnds[Math.min(n - 1, Math.max(0, Math.round(frac * (n - 1))))];
    this.seabed.setVisible(te.seabed); this.layers.setVisible(te.layers); this.trackLine.geometry.setDrawRange(0, te.trk);
    this._placeVehicle(...te.p); this._showAnoms(te.t);
  }
  _showAnoms(t) { for (const a of this.anomalies.values()) a.group.visible = a.first_seen <= t; }

  // ---- viewport ----------------------------------------------------------------------------------
  /** Shift the optical centre by (dx, dy) css px so the scene sits in the visible gap between floating panels. */
  setViewOffset(dx, dy) { this.offset = [dx, dy]; this.resize(); }
  resize() {
    const w = this.el.clientWidth || 300, h = this.el.clientHeight || 300;
    this.renderer.setSize(w, h, false); this.renderer.domElement.style.cssText = 'width:100%;height:100%;display:block';
    this.camera.aspect = w / h;
    const [dx, dy] = this.offset;
    if (dx || dy) this.camera.setViewOffset(w, h, -dx, -dy, w, h); else this.camera.clearViewOffset();
    this.camera.updateProjectionMatrix();
  }
  setActive(on) { this.active = on; }

  _frame() {
    if (!this.active) return;
    const now = performance.now(), k = now / 1000;
    if (this._fly) {
      const f = Math.min(1, (now - this._fly.t0) / this._fly.dur), e = f < .5 ? 2 * f * f : 1 - Math.pow(-2 * f + 2, 2) / 2;
      this.camera.position.lerpVectors(this._fly.p0, this._fly.p1, e); this.controls.target.lerpVectors(this._fly.t0v, this._fly.t1, e);
      if (f >= 1) this._fly = null;
    }
    for (const a of this.anomalies.values()) a.group.children.forEach(c => { if (c.userData.pulse) c.material.opacity = .26 + .1 * Math.sin(k * 3); });
    if (this.hl) { const s = 1 + .04 * Math.sin(k * 4); this.hl.userData.ring.scale.set(s, s, s); }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
