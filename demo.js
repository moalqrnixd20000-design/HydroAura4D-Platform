// Offline data source: replays a mission recorded from the real backend (tools/record_demo.py).
// Used automatically when no backend is reachable (e.g. the static GitHub Pages build) or with ?demo.
// Exposes the same surface as LiveSource so the dashboard cannot tell them apart.
export class DemoSource {
  constructor(h, url = 'demo/mission.json.gz') {
    this.h = h; this.url = url; this.kind = 'demo';
    this.data = null; this.i = 0; this.paused = false; this.speed = 4; this.timer = null; this.t = 0;
  }

  async start() {
    this.h.status('loading');
    if (!this.data) this.data = await this._load();
    this.h.status('demo');
    this.restart();
  }

  async _load() {
    const res = await fetch(this.url);
    if (!res.ok) throw new Error(`demo data not found (${res.status})`);
    if (this.url.endsWith('.gz') && 'DecompressionStream' in window && res.headers.get('content-encoding') !== 'gzip') {
      const ds = res.body.pipeThrough(new DecompressionStream('gzip'));
      return JSON.parse(await new Response(ds).text());
    }
    return res.json();
  }

  restart() {
    clearTimeout(this.timer);
    this.i = 0; this.paused = false;
    this.h.hello(structuredClone(this.data.hello));
    this._schedule();
  }

  _schedule() {
    clearTimeout(this.timer);
    const dt = this.data.dt_sim / this.speed;                // real seconds per recorded tick
    this.timer = setTimeout(() => this._tick(), dt * 1000);
  }

  _tick() {
    if (this.paused) return;
    if (this.i >= this.data.ticks.length) { this.h.status('demo-done'); return; }
    this.h.tick(this.data.ticks[this.i++]);
    this._schedule();
  }

  // ---- controls (mirror the REST controls of the live backend)
  async pause(on) { this.paused = on; if (!on) this._schedule(); }
  async setSpeed(x) { this.speed = x; if (!this.paused) this._schedule(); }
  async reset() { this.restart(); }
  stop() { clearTimeout(this.timer); }
}

export class LiveSource {
  constructor(h, { http = '', wsUrl }) {
    this.h = h; this.http = http; this.wsUrl = wsUrl; this.kind = 'live';
    this.ws = null; this.retry = 0; this.everOpen = false; this.stopped = false;
  }

  /** Resolves true when the socket opens; false if the backend is unreachable (caller may fall back to demo). */
  start() {
    return new Promise(resolve => {
      let settled = false;
      const done = v => { if (!settled) { settled = true; resolve(v); } };
      const open = () => {
        if (this.stopped) return;
        this.h.status('connecting');
        const ws = this.ws = new WebSocket(this.wsUrl);
        ws.onopen = () => { this.retry = 0; this.everOpen = true; this.h.status('live'); done(true); };
        ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.type === 'hello') this.h.hello(m); else if (m.type === 'tick') this.h.tick(m); };
        ws.onclose = () => {
          this.h.status('offline');
          if (!this.everOpen) { done(false); return; }              // never connected: let the caller decide
          setTimeout(open, Math.min(5000, 500 * 2 ** this.retry++));
        };
        ws.onerror = () => ws.close();
      };
      open();
      setTimeout(() => done(this.everOpen), 3000);
    });
  }

  _post(path) { return fetch(this.http + path, { method: 'POST' }).then(r => r.json()); }
  pause(on) { return this._post(`/api/pause?paused=${on}`); }
  setSpeed(x) { return this._post(`/api/speed/${x}`); }
  async reset() { await this._post('/api/reset'); this.ws && this.ws.close(); }
  stop() { this.stopped = true; this.ws && this.ws.close(); }
}
