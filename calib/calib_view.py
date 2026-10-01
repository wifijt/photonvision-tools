#!/usr/bin/env python3
#
# Copyright (C) 2026  wifijt
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.  This program is distributed WITHOUT ANY WARRANTY; see the GNU
# General Public License at <https://www.gnu.org/licenses/> for details.
#
"""Mirrored camera view for ChArUco calibration.

    python3 calib_view.py --host photonvision.local
    open http://localhost:8078

You stand in front of the camera holding the board, so moving it to YOUR left
puts it on the RIGHT of the image. Every correction you make is backwards, and
filling the frame becomes a puzzle instead of a task. This mirrors the view so
the board moves the way your hands do.

The mirror is CSS on the displayed image only - `transform: scaleX(-1)`. The
frames PhotonVision actually calibrates from are untouched, which matters: a
flipped image would produce a flipped calibration, and the camera's own
`inputImageRotationMode` must stay at DEG_0 because a non-zero value corrupts
the multi-tag pose while leaving the published corners correct (PhotonVision
issue #2613). Mirror the human, never the sensor.

It shows the OUTPUT stream, which is the one with the detection overlays drawn
on it, and reports how many calibration observations PhotonVision has actually
KEPT - not how many you have shot. Those are different numbers, and only the
first one counts.
"""
import argparse, asyncio, json, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import msgpack, websockets
except ImportError:
    sys.exit("need: pip install msgpack websockets")

STATE = {"cams": [], "host": "", "error": None}
LOCK = threading.Lock()


def poll(host, port):
    """Keep the camera list and calibration progress fresh."""
    async def once():
        uri = "ws://%s:%d/websocket_data" % (host, port)
        async with websockets.connect(uri, open_timeout=15, ping_interval=None,
                                      max_size=80_000_000) as ws:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
                if not isinstance(raw, bytes):
                    continue
                m = msgpack.unpackb(raw, raw=False)
                if not isinstance(m, dict):
                    continue
                cs = m.get("cameraSettings")
                if not cs:
                    continue
                out = []
                for c in cs:
                    st = c.get("currentPipelineSettings", {})
                    idx = st.get("cameraVideoModeIndex")
                    fmts = c.get("videoFormatList") or {}
                    fmt = fmts.get(idx) or fmts.get(str(idx)) or {}
                    cals = c.get("calibrations") or []
                    here = None
                    for cal in cals:
                        r = cal.get("resolution") or {}
                        if (fmt and abs(float(r.get("width", -1)) - float(fmt.get("width", 0))) < 1
                                and abs(float(r.get("height", -1)) - float(fmt.get("height", 0))) < 1):
                            here = cal
                    out.append({
                        "nickname": c.get("nickname"),
                        "output": c.get("outputStreamPort"),
                        "input": c.get("inputStreamPort"),
                        "width": fmt.get("width"), "height": fmt.get("height"),
                        "calibrated": here is not None,
                        "snapshots": (here or {}).get("numSnapshots"),
                        "meanErr": (sum((here or {}).get("meanErrors") or [0]) /
                                    max(1, len((here or {}).get("meanErrors") or [1])))
                                   if here else None,
                        "resolutions": ["%gx%g" % ((c2.get("resolution") or {}).get("width", 0),
                                                   (c2.get("resolution") or {}).get("height", 0))
                                        for c2 in cals],
                    })
                with LOCK:
                    STATE["cams"] = out
                    STATE["error"] = None

    while True:
        try:
            asyncio.new_event_loop().run_until_complete(once())
        except Exception as exc:
            with LOCK:
                STATE["error"] = "%s: %s" % (type(exc).__name__, exc)
            time.sleep(3)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/data"):
            with LOCK:
                body = json.dumps({"cams": STATE["cams"], "host": STATE["host"],
                                   "error": STATE["error"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


PAGE = r"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Calibration View</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Saira+Condensed:wght@600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{--bg:#0E1417;--panel:#161F24;--ink:#E6EDF0;--dim:#8DA0A9;--line:#2A3940;
      --ok:#5FBF83;--warn:#E8A33D;--bad:#E06A5A;--accent:#4FB0D0;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"IBM Plex Mono",monospace}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:10px 16px;
       border-bottom:1px solid var(--line);background:var(--panel)}
h1{font-family:"Saira Condensed",sans-serif;font-size:20px;margin:0;letter-spacing:.02em}
button{font:inherit;background:transparent;color:var(--dim);border:1px solid var(--line);
       border-radius:3px;padding:4px 10px;cursor:pointer}
button.on{color:var(--accent);border-color:var(--accent)}
#wrap{position:relative;width:100%;background:#000;display:flex;justify-content:center}
#cam{display:block;max-width:100%;max-height:calc(100vh - 150px)}
#cam.mir{transform:scaleX(-1)}
#grid{position:absolute;inset:0;pointer-events:none}
.pill{font-size:11px;letter-spacing:.05em;padding:3px 8px;border:1px solid var(--line);
      border-radius:2px;color:var(--dim)}
.pill.ok{color:var(--ok);border-color:var(--ok)}
.pill.bad{color:var(--bad);border-color:var(--bad)}
.note{color:var(--dim);font-size:12px;padding:8px 16px;line-height:1.5}
</style>
<header>
  <h1>Calibration View</h1>
  <span id="pick"></span>
  <button id="mir" class="on">mirrored</button>
  <button id="grd" class="on">grid</button>
  <span class="pill" id="res">&mdash;</span>
  <span class="pill" id="cal">&mdash;</span>
</header>
<div id="wrap"><img id="cam" alt="camera"><svg id="grid"></svg></div>
<p class="note" id="hint"></p>
<script>
const $=s=>document.querySelector(s);
let D={cams:[]}, sel=0, mir=true, grd=true;
function drawGrid(){
  const img=$('#cam'), g=$('#grid');
  const w=img.clientWidth||0, h=img.clientHeight||0;
  g.setAttribute('width',w); g.setAttribute('height',h);
  g.style.left=(img.offsetLeft)+'px'; g.style.width=w+'px'; g.style.height=h+'px';
  if(!grd||!w){g.innerHTML='';return;}
  let s='';
  for(let i=1;i<3;i++){
    s+=`<line x1="${w*i/3}" y1="0" x2="${w*i/3}" y2="${h}" stroke="#4FB0D0" stroke-opacity=".35" stroke-width="1"/>`;
    s+=`<line x1="0" y1="${h*i/3}" x2="${w}" y2="${h*i/3}" stroke="#4FB0D0" stroke-opacity=".35" stroke-width="1"/>`;
  }
  s+=`<rect x="1" y="1" width="${w-2}" height="${h-2}" fill="none" stroke="#4FB0D0" stroke-opacity=".5"/>`;
  g.innerHTML=s;
}
function apply(){
  const c=D.cams[sel]; if(!c)return;
  const url=`http://${D.host}:${c.output}/stream.mjpg`;
  const img=$('#cam');
  if(img.dataset.url!==url){img.dataset.url=url;img.src=url;}
  img.className = mir?'mir':'';
  $('#res').textContent = c.width?`${c.width}x${c.height}`:'—';
  const cal=$('#cal');
  if(c.calibrated){
    cal.textContent = `calibrated · ${c.snapshots??'?'} kept`
      + (c.meanErr!=null?` · ${c.meanErr.toFixed(3)} px`:'');
    cal.className='pill ok';
  }else{
    cal.textContent = `NOT calibrated at this resolution`;
    cal.className='pill bad';
  }
  $('#hint').textContent = mir
    ? 'Mirrored: the board moves the way your hands do. The frames PhotonVision calibrates from are NOT flipped.'
    : 'Unmirrored: this is the raw sensor view, so your left is the image’s right.';
  drawGrid();
}
function pick(){
  $('#pick').innerHTML = D.cams.map((c,i)=>
    `<button data-i="${i}" class="${i===sel?'on':''}">${c.nickname}</button>`).join(' ');
  $('#pick').querySelectorAll('button').forEach(b=>b.onclick=()=>{
    sel=+b.dataset.i; pick(); apply();});
}
$('#mir').onclick=()=>{mir=!mir;$('#mir').className=mir?'on':'';apply();};
$('#grd').onclick=()=>{grd=!grd;$('#grd').className=grd?'on':'';drawGrid();};
addEventListener('resize',drawGrid);
$('#cam').addEventListener('load',drawGrid);
async function tick(){
  try{
    const r=await fetch('/data',{cache:'no-store'}); const j=await r.json();
    const first = D.cams.length===0;
    D=j; if(first){pick();} else {
      $('#pick').querySelectorAll('button').forEach((b,i)=>b.className=(i===sel?'on':''));
    }
    apply();
  }catch(e){}
}
tick(); setInterval(tick,2000);
</script>
"""


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="photonvision.local")
    p.add_argument("--port", type=int, default=5800, help="PhotonVision API port")
    p.add_argument("--serve", type=int, default=8078, help="local port for this viewer")
    a = p.parse_args()
    STATE["host"] = a.host
    threading.Thread(target=poll, args=(a.host, a.port), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.serve), H)
    print("mirrored calibration view:  http://localhost:%d" % a.serve)
    print("  (the mirror is display-only - PhotonVision's frames are untouched)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
