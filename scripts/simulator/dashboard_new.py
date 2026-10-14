#!/usr/bin/env python3
"""Minimal web dashboard for the mote_7 FreeRTOS pipeline.

  python3 dashboard_new.py        -> open http://127.0.0.1:8000
  UDP 5000  compressed packets  (28-byte keyframe 'FR', or 6-byte delta)
  UDP 5001  raw rows            ("!4sQqffff", magic RAW1)
  UDP 5002  node telemetry      (JSON: health report every second, light events)

Standard library only. The page updates live over Server-Sent Events; nothing is loaded from the internet.
Decoding logic, constants and statistics match the terminal dashboard; raw and decoded samples are paired
per sample ID with a FIFO per ID (epochs repeat/reset in this dataset, a plain dict would overwrite them)."""
import argparse, collections, json, select, socket, struct, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COMPRESSED_PORT, RAW_PORT, TELEMETRY_PORT = 5000, 5001, 5002
RAW_FORMAT, RAW_MAGIC = "!4sQqffff", b"RAW1"
MAGIC, VERSION = b"FR", 7
RAW_FLOAT_BYTES_PER_SAMPLE = 12          # 3 x float32 (temperature, humidity, light)
SENSOR_BYTES_PER_DELTA, KEYFRAME_SENSOR_BYTES = 5, 8
IPV4_UDP_HEADER_BYTES = 28               # estimate only
SENSORS = ("temperature", "humidity", "light")


def _signed(v, width):
    return v - (1 << width) if v & (1 << (width - 1)) else v


class V7Decoder:
    """Stateful decoder: returns (sid, ts_or_None, t, h, hres, light, is_keyframe)."""
    def __init__(self): self.st = None

    def decode(self, p):
        if len(p) == 28 and p[:2] == MAGIC and p[2] == VERSION and p[3] == 0:
            sid, ts, t, h, hres, light = struct.unpack_from("<QQHHhH", p, 4)
            self.st = dict(sid=sid, t=t, h=h, light=light)
            return sid, ts, t, h, hres, light, True
        if len(p) != 6 or self.st is None:
            raise ValueError("invalid delta packet or keyframe not received")
        if p[0] == 0:
            raise ValueError("delta packet with zero sample-ID delta")
        bits = int.from_bytes(p[1:6], "big")
        dlight, dhum = _signed((bits >> 27) & 0x1FFF, 13), _signed((bits >> 16) & 0x7FF, 11)
        hres_q, dtemp = _signed((bits >> 11) & 0x1F, 5), _signed(bits & 0x7FF, 11)
        s = self.st
        sid, t, h, light, hres = s["sid"] + p[0], s["t"] + dtemp, s["h"] + dhum, s["light"] + dlight, hres_q * 1000
        if not (0 <= t <= 65535 and 0 <= h <= 65535 and 0 <= light <= 65535 and -32768 <= hres <= 32767):
            raise ValueError("decoded sensor value out of range")
        self.st = dict(sid=sid, t=t, h=h, light=light)
        return sid, None, t, h, hres, light, False


class State:
    def __init__(self, light_threshold):
        self.lock = threading.Lock(); self.dec = V7Decoder(); self.thr = light_threshold
        self.raw_q = collections.defaultdict(collections.deque); self.dec_q = collections.defaultdict(collections.deque)
        self.c = dict(frames=0, packets=0, errors=0, raw_datagrams=0, keyframes=0, deltas=0, sensor_raw=0, sensor_enc=0, payload=0,
                      matched=0, events=0)
        self.err_sum = dict.fromkeys(SENSORS, 0.0); self.err_max = dict.fromkeys(SENSORS, 0.0)
        self.sep = collections.deque(maxlen=500); self.prev_light = None
        self.latest = None; self.new_points = []; self.history = collections.deque(maxlen=300)
        self.log = collections.deque(maxlen=8); self.first = self.last = None; self.last_error = ""
        self.health = None; self.health_at = None; self.node_events = collections.deque(maxlen=8)

    # ---- node telemetry (health + light events) -------------------------------------------------
    def on_telemetry(self, pkt):
        with self.lock:
            try:
                msg = json.loads(pkt)
            except ValueError:
                self.c["errors"] += 1; return
            if msg.get("type") == "health":
                self.health, self.health_at = msg, time.time()
            elif msg.get("type") == "event":
                msg["at"] = time.time(); self.node_events.appendleft(msg)

    # ---- raw feed -------------------------------------------------------------------------------
    def on_raw(self, pkt):
        with self.lock:
            try:
                magic, sid, ts, temp, hum, light, volt = struct.unpack(RAW_FORMAT, pkt)
                if magic != RAW_MAGIC: raise ValueError("bad raw magic")
            except (struct.error, ValueError):
                self.c["errors"] += 1; return
            now = time.monotonic(); self.c["raw_datagrams"] += 1; self._touch()
            if self.prev_light is not None and ((self.prev_light < self.thr <= light) or (self.prev_light >= self.thr > light)):
                self.c["events"] += 1
            self.prev_light = light
            self.raw_q[sid].append(dict(ts=ts, temperature=temp, humidity=hum, light=light, voltage=volt, at=now))
            self._pair(sid)

    # ---- compressed feed ------------------------------------------------------------------------
    def on_packet(self, pkt):
        with self.lock:
            now = time.monotonic()
            try:
                sid, ts, t, h, hres, light, key = self.dec.decode(pkt)
            except (ValueError, struct.error, OverflowError) as e:
                self.c["errors"] += 1; self.last_error = str(e); return
            c = self.c; c["packets"] += 1; c["frames"] += 1; self._touch()
            c["keyframes" if key else "deltas"] += 1
            c["sensor_raw"] += RAW_FLOAT_BYTES_PER_SAMPLE
            c["sensor_enc"] += KEYFRAME_SENSOR_BYTES if key else SENSOR_BYTES_PER_DELTA
            c["payload"] += len(pkt)
            self.log.appendleft(dict(sid=sid, kind="KEY" if key else "DELTA", size=len(pkt)))
            self.dec_q[sid].append(dict(temperature=t * 0.0098, humidity=h * 0.34 + hres / 10000.0, light=light * 0.46, at=now))
            self._pair(sid)

    def _touch(self):
        self.last = time.time(); self.first = self.first or self.last

    def _pair(self, sid):
        while self.raw_q[sid] and self.dec_q[sid]:
            r, d = self.raw_q[sid].popleft(), self.dec_q[sid].popleft()
            self.sep.append(abs(d["at"] - r["at"]) * 1000.0); self.c["matched"] += 1
            for k in SENSORS:
                e = abs(d[k] - r[k]); self.err_sum[k] += e; self.err_max[k] = max(self.err_max[k], e)
            p = dict(sid=sid, ts=r["ts"], raw=[r[k] for k in SENSORS] + [r["voltage"]], dec=[d[k] for k in SENSORS])
            self.latest = p; self.new_points.append(p); self.history.append(p)

    # ---- snapshot for the browser ---------------------------------------------------------------
    def snapshot(self, full=False):
        with self.lock:
            c, n = self.c, max(self.c["matched"], 1)
            enc = c["sensor_enc"]
            snap = dict(
                now=time.time(), live=bool(self.last and time.time() - self.last < 5), uptime=(self.last - self.first) if self.first else 0,
                counters=dict(c), ratio=(c["sensor_raw"] / enc) if enc else None,
                saved_pct=(100.0 * (c["sensor_raw"] - enc) / c["sensor_raw"]) if c["sensor_raw"] else None,
                ipv4_estimate=c["payload"] + c["packets"] * IPV4_UDP_HEADER_BYTES,
                mae={k: self.err_sum[k] / n for k in SENSORS}, maxerr=dict(self.err_max),
                sep_avg=(sum(self.sep) / len(self.sep)) if self.sep else None, sep_max=max(self.sep) if self.sep else None,
                unmatched_raw=sum(len(q) for q in self.raw_q.values()), unmatched_dec=sum(len(q) for q in self.dec_q.values()),
                latest=self.latest, log=list(self.log), last_error=self.last_error, threshold=self.thr,
                health=self.health, health_age=(time.time() - self.health_at) if self.health_at else None,
                node_events=list(self.node_events))
            snap["points"] = list(self.history) if full else self.new_points
            if not full: self.new_points = []
            return snap


def udp_loop(st):
    socks = {}
    handlers = {COMPRESSED_PORT: st.on_packet, RAW_PORT: st.on_raw, TELEMETRY_PORT: st.on_telemetry}
    for port in handlers:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20); s.bind(("0.0.0.0", port)); socks[s] = port
    while True:
        for s in select.select(list(socks), [], [], 0.5)[0]:
            data, _ = s.recvfrom(8192)
            handlers[socks[s]](data)


class Handler(BaseHTTPRequestHandler):
    state = None; subs = []; subs_lock = threading.Lock()
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/events":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache"); self.end_headers()
            import queue; q = queue.Queue(maxsize=20)
            with Handler.subs_lock: Handler.subs.append(q)
            try:
                self.wfile.write(("event: init\ndata: " + json.dumps(self.state.snapshot(full=True)) + "\n\n").encode()); self.wfile.flush()
                while True: self.wfile.write(("data: " + q.get() + "\n\n").encode()); self.wfile.flush()
            except Exception: pass
            finally:
                with Handler.subs_lock:
                    if q in Handler.subs: Handler.subs.remove(q)
        elif self.path == "/snapshot":
            b = json.dumps(self.state.snapshot(full=True)).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
        else:
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(PAGE.encode())


def broadcast_loop(st, period):
    import queue
    while True:
        time.sleep(period); msg = json.dumps(st.snapshot())
        with Handler.subs_lock:
            for q in Handler.subs:
                try: q.put_nowait(msg)
                except queue.Full: pass


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mote 7 · stream dashboard</title><style>
:root{--bg:#f5f6f8;--card:#fff;--fg:#17202e;--mut:#6b7686;--line:#e3e6ea;--t:#e4572e;--h:#2e86de;--l:#d4a017;--ok:#16794c;--bad:#b42318}
@media(prefers-color-scheme:dark){:root{--bg:#0e1116;--card:#161a21;--fg:#e8ebf0;--mut:#8b95a5;--line:#252b35;--t:#ff7a59;--h:#5aa9ff;--l:#f0c040;--ok:#4ade80;--bad:#ff6b6b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 40px}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
h1{font-size:18px;font-weight:650;margin:0}.sub{color:var(--mut);font-size:12px}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:3px 10px;border:1px solid var(--line);border-radius:99px;background:var(--card)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--mut)}.live .dot{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent)}
.grid{display:grid;gap:12px}.kpis{grid-template-columns:repeat(auto-fit,minmax(200px,1fr));margin-bottom:12px}
.sensors{grid-template-columns:repeat(auto-fit,minmax(300px,1fr));margin-bottom:12px}.two{grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.lbl{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);margin-bottom:6px}
.kpi{font-size:26px;font-weight:650;letter-spacing:-.02em}.kpi small{font-size:13px;font-weight:500;color:var(--mut);margin-left:4px}
.val{font-size:30px;font-weight:650;letter-spacing:-.02em;font-variant-numeric:tabular-nums}.val small{font-size:14px;color:var(--mut);font-weight:500;margin-left:3px}
.row{display:flex;justify-content:space-between;gap:8px;padding:3px 0;font-variant-numeric:tabular-nums}.row span:first-child{color:var(--mut)}
canvas{width:100%;height:84px;display:block;margin:8px 0 4px}
.bar{height:10px;border-radius:5px;background:var(--line);overflow:hidden;margin:4px 0 10px}.bar i{display:block;height:100%}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}td,th{padding:5px 4px;text-align:right;border-bottom:1px solid var(--line);font-weight:400}
th{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.05em}td:first-child,th:first-child{text-align:left}tr:last-child td{border-bottom:0}
.ok{color:var(--ok)}.bad{color:var(--bad)}.k{display:inline-block;font-size:11px;padding:1px 7px;border-radius:5px;border:1px solid var(--line)}.k.key{color:var(--l)}
footer{color:var(--mut);font-size:12px;margin-top:14px}
</style></head><body><div class="wrap">
<header><div><h1>Mote 7 · compressed sensor stream</h1><div class="sub" id="sub">waiting for data on UDP 5000 / 5001…</div></div><span class="pill" id="live"><span class="dot"></span><span id="livetxt">no data</span></span></header>
<div class="grid kpis" id="kpis"></div>
<div class="grid sensors" id="sensors"></div>
<div class="grid two">
 <div class="card"><div class="lbl">Compression</div><div id="comp"></div></div>
 <div class="card"><div class="lbl">Accuracy · decoded vs raw</div><table id="acc"></table></div>
 <div class="card"><div class="lbl">Link &amp; health</div><div id="link"></div></div>
 <div class="card"><div class="lbl">Recent packets</div><table id="log"></table></div>
</div>
<div class="card" style="margin-top:12px"><div class="lbl">Scheduling · task timing (measured on the node)</div><div style="overflow-x:auto"><table id="tasks"></table></div><div id="sched"></div></div>
<div class="grid two" style="margin-top:12px">
 <div class="card"><div class="lbl">System health (node)</div><div id="health"></div></div>
 <div class="card"><div class="lbl">Light events (node, threshold 106 ± 5 lux)</div><table id="nevents"></table></div>
</div>
<footer>Delta packets omit timestamps; raw and compressed samples are paired by sample ID. Humidity residual is quantized in delta packets, so humidity shows a small, bounded error.</footer>
</div><script>
const S=[["temperature","Temperature","°C","var(--t)",4],["humidity","Humidity","%","var(--h)",4],["light","Light","lux","var(--l)",2]];
const $=id=>document.getElementById(id),f=(x,d=2)=>x==null?"–":Number(x).toFixed(d),n=x=>x==null?"–":Number(x).toLocaleString();
const hist=[];let snap=null;
function build(){$("sensors").innerHTML=S.map(([k,name,u,c])=>`<div class="card"><div class="lbl">${name}</div><div class="val" id="r_${k}" style="color:${c}">–<small>${u}</small></div>
<canvas id="g_${k}" width="640" height="170"></canvas><div class="row"><span>decoded</span><span id="d_${k}">–</span></div><div class="row"><span>|error|</span><span id="e_${k}">–</span></div></div>`).join("");}
function spark(i,color){const c=$("g_"+S[i][0]),x=c.getContext("2d"),W=c.width,H=c.height;x.clearRect(0,0,W,H);if(hist.length<2)return;
 const R=hist.map(p=>p.raw[i]),D=hist.map(p=>p.dec[i]),all=R.concat(D),mn=Math.min(...all),mx=Math.max(...all),sp=(mx-mn)||1,cs=getComputedStyle(document.body);
 const line=(a,col,w,al)=>{x.globalAlpha=al;x.strokeStyle=col;x.lineWidth=w;x.beginPath();a.forEach((v,j)=>{const X=j/(a.length-1)*W,Y=H-8-(v-mn)/sp*(H-16);j?x.lineTo(X,Y):x.moveTo(X,Y)});x.stroke();x.globalAlpha=1};
 line(R,cs.getPropertyValue("--mut"),3,.7);line(D,color,3,1);}
function render(){if(!snap)return;const c=snap.counters;
 $("live").className="pill"+(snap.live?" live":"");$("livetxt").textContent=snap.live?"live":"no data";
 $("sub").textContent=snap.latest?`latest sample ID ${snap.latest.sid} · ${new Date(snap.latest.ts).toLocaleString()}`:"waiting for data on UDP 5000 / 5001…";
 $("kpis").innerHTML=[["Compression ratio",snap.ratio?f(snap.ratio,2)+"×":"–","sensor bytes, vs 3×float32"],["Bytes saved",snap.saved_pct==null?"–":f(snap.saved_pct,1)+"%","sensor fields only"],["Samples decoded",n(c.frames),n(c.packets)+" packets"],["Decode errors",n(c.errors),snap.last_error||"none"]]
 .map(([a,b,s])=>`<div class="card"><div class="lbl">${a}</div><div class="kpi ${a=="Decode errors"&&c.errors?"bad":""}">${b}</div><div class="sub">${s}</div></div>`).join("");
 if(snap.latest)S.forEach(([k,,,col,d],i)=>{$("r_"+k).firstChild.nodeValue=f(snap.latest.raw[i],d);$("d_"+k).textContent=f(snap.latest.dec[i],d);$("e_"+k).textContent=f(Math.abs(snap.latest.dec[i]-snap.latest.raw[i]),d+1);spark(i,getComputedStyle(document.body).getPropertyValue(col.slice(4,-1)))});
 const raw=c.sensor_raw,enc=c.sensor_enc,pct=raw?100*enc/raw:0;
 $("comp").innerHTML=`<div class="row"><span>raw baseline</span><span>${n(raw)} B</span></div><div class="bar"><i style="width:100%;background:var(--mut)"></i></div>
 <div class="row"><span>encoded sensor data</span><span>${n(enc)} B</span></div><div class="bar"><i style="width:${pct}%;background:var(--ok)"></i></div>
 <div class="row"><span>keyframes / deltas</span><span>${n(c.keyframes)} / ${n(c.deltas)}</span></div>
 <div class="row"><span>UDP payload (incl. metadata)</span><span>${n(c.payload)} B</span></div><div class="row"><span>IPv4+UDP estimate</span><span>${n(snap.ipv4_estimate)} B</span></div>`;
 $("acc").innerHTML="<tr><th></th><th>MAE</th><th>max</th></tr>"+S.map(([k,name,u,,d])=>`<tr><td>${name}</td><td>${f(snap.mae[k],5)} ${u}</td><td>${f(snap.maxerr[k],5)} ${u}</td></tr>`).join("");
 $("link").innerHTML=`<div class="row"><span>raw datagrams</span><span>${n(c.raw_datagrams)}</span></div><div class="row"><span>matched samples</span><span>${n(c.matched)}</span></div>
 <div class="row"><span>unmatched raw / decoded</span><span class="${snap.unmatched_raw>5||snap.unmatched_dec>5?"bad":""}">${snap.unmatched_raw} / ${snap.unmatched_dec}</span></div>
 <div class="row"><span>raw↔compressed arrival gap</span><span>${f(snap.sep_avg,1)} / ${f(snap.sep_max,1)} ms</span></div>
 <div class="row"><span>light events (&gt;${snap.threshold} lux crossings)</span><span>${n(c.events)}</span></div><div class="row"><span>uptime</span><span>${Math.round(snap.uptime)} s</span></div>`;
 $("log").innerHTML="<tr><th>sample ID</th><th>type</th><th>bytes</th></tr>"+snap.log.map(l=>`<tr><td>${l.sid}</td><td><span class="k ${l.kind=="KEY"?"key":""}">${l.kind}</span></td><td>${l.size}</td></tr>`).join("");}
function renderHealth(){const h=snap.health;if(!h){$("health").innerHTML='<div class="row"><span>waiting for node telemetry on UDP 5002…</span></div>';return;}
 const age=snap.health_age,stale=age==null||age>3;
 $("tasks").innerHTML="<tr><th>task</th><th>prio</th><th>period</th><th>deadline</th><th>jobs</th><th>misses</th><th>overruns</th><th>C avg / max (us)</th><th>jitter max (us)</th><th>response max (us)</th><th>CPU %</th></tr>"+
  h.tasks.map(t=>`<tr><td>${t[0]}</td><td>${t[1]}</td><td>${t[2]?t[2]+" ms":"event"}</td><td>${f(t[3]/1000,t[3]<10000?1:0)} ms</td><td>${n(t[4])}</td><td class="${t[5]?"bad":"ok"}">${t[5]}</td><td class="${t[6]?"bad":"ok"}">${t[6]}</td><td>${f(t[7],1)} / ${f(t[8],1)}</td><td>${f(t[9],0)}</td><td>${f(t[10],0)}</td><td>${f(t[11],2)}</td></tr>`).join("");
 const sc=h.sched,ok=sc.ud<=sc.bound;
 $("sched").innerHTML=`<div class="row" style="margin-top:8px"><span>schedulability (measured Cmax): U = Σ C/T · Ud = Σ C/D vs bound n(2^(1/n)−1)</span><span class="${ok?"ok":"bad"}">U ${f(sc.u,4)} · Ud ${f(sc.ud,4)} / ${f(sc.bound,4)}</span></div>
 <div class="row"><span>priorities: Rate-Monotonic (the 3 sensors share one period, so they share one priority)</span><span>tick ${f(h.tick_hz,1)} Hz</span></div>`;
 const q=h.q,cnt=v=>v?`<span class="bad">${v}</span>`:'<span class="ok">0</span>';
 $("health").innerHTML=`<div class="row"><span>node status</span><span class="${stale?"bad":"ok"}">${stale?"stale":"alive"} · uptime ${f(h.up_s,0)} s</span></div>
 <div class="row"><span>readingQueue level / peak / size · drops</span><span>${q.reading[0]} / ${q.reading[1]} / ${q.reading[2]} · ${cnt(q.reading[3])}</span></div>
 <div class="row"><span>netQueue level / peak / size · drops</span><span>${q.net[0]} / ${q.net[1]} / ${q.net[2]} · ${cnt(q.net[3])}</span></div>
 <div class="row"><span>polls T/H/L (every ${h.period_ms} ms = 10 s of dataset time at ${h.speed}×)</span><span>${h.sensors.polls.join(" / ")}</span></div>
 <div class="row"><span>new values T/H/L</span><span>${h.sensors.new.join(" / ")}</span></div>
 <div class="row"><span>frames: total · complete · partial (carried forward)</span><span>${n(h.frames.total)} · ${n(h.frames.complete)} · ${cnt(h.frames.partial)}</span></div>
 <div class="row"><span>keyframes · compressed bytes · encode errors</span><span>${n(h.frames.keyframes)} · ${n(h.frames.bytes)} · ${cnt(h.frames.errors)}</span></div>
 <div class="row"><span>end-to-end latency (read → sent) avg / max</span><span>${f(h.lat.e2e_avg_ms,2)} / ${f(h.lat.e2e_max_ms,2)} ms</span></div>
 <div class="row"><span>light event handling max</span><span>${f(h.lat.event_max_us,1)} µs</span></div>
 <div class="row"><span>UDP sent · errors</span><span>${n(h.udp.sent)} · ${cnt(h.udp.errors)}</span></div>`;
 $("nevents").innerHTML="<tr><th>sample ID</th><th>edge</th><th>lux</th><th>handling (us)</th></tr>"+(snap.node_events.length?snap.node_events.map(e=>
  `<tr><td>${e.sid}</td><td>${e.dir=="rising"?"lights ON ↑":"lights OFF ↓"}</td><td>${f(e.lux,2)}</td><td>${f(e.latency_us,1)}</td></tr>`).join(""):'<tr><td colspan="4">none yet</td></tr>');}
build();
/* ?static: render one snapshot only (no live stream), e.g. for screenshots */
if(location.search.includes("static")){fetch("/snapshot").then(r=>r.json()).then(x=>{snap=x;hist.push(...snap.points);render();renderHealth()});}
const es=location.search.includes("static")?{addEventListener(){}}:new EventSource("/events");
es.addEventListener("init",e=>{snap=JSON.parse(e.data);hist.push(...snap.points);render();renderHealth()});
es.onmessage=e=>{snap=JSON.parse(e.data);hist.push(...snap.points);while(hist.length>240)hist.shift();render();renderHealth()};
es.onerror=()=>{$("livetxt").textContent="disconnected"};
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8000); ap.add_argument("--light-threshold", type=float, default=106.0)
    a = ap.parse_args()
    st = State(a.light_threshold); Handler.state = st
    threading.Thread(target=udp_loop, args=(st,), daemon=True).start()
    threading.Thread(target=broadcast_loop, args=(st, 0.25), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler); srv.daemon_threads = True
    print(f"dashboard: http://127.0.0.1:{a.port}   (listening on UDP {COMPRESSED_PORT} compressed, {RAW_PORT} raw, {TELEMETRY_PORT} node telemetry)  Ctrl+C to stop")
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nstopped")


if __name__ == "__main__":
    main()
