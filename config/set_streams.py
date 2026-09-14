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
"""Turn PhotonVision's camera streams off for a match, back on for the pit.

PhotonVision copies, converts, draws on and encodes its camera streams every
frame even when nothing is connected.  On a competition robot - laptop unplugged,
nobody looking - that is pure waste.  Measured on a Pi 5 with an OV9281 at
1280x800, freshly restarted, nothing attached to any stream port:

    streams on   60.1 fps   42.0 ms latency   frame period 17.42 ms
    streams off  80.5 fps   32.7 ms latency   frame period  8.71 ms

A third of the framerate, for a picture no one is watching.  8.71 ms is the
sensor's own period: with streams off the pipeline keeps up with every camera
frame, with them on it drops every other one.  An actual viewer costs ~2.5 ms -
it is the unconditional preparation that is expensive, not the viewing.

Measure this on a freshly restarted service; a long-running process drifts and
distorts the comparison.

The dashboard cannot do this - its stream selector requires at least one stream
to stay selected - so the only way to turn both off is the websocket API, which
is what this does.  It applies live, with no service restart.

    set_streams.py --status
    set_streams.py --off                  # match: no streams
    set_streams.py --on                   # pit: processed stream back
    set_streams.py --daemon               # let the robot decide, over NT

In daemon mode it watches a NetworkTables boolean and applies it, so the robot
can shed the streams itself when the match starts:

    /PhotonStreams/enable     robot writes: true = streams on, false = off
    /PhotonStreams/state      we publish: what is actually set right now
    /PhotonStreams/latencyMs  we publish: measured capture->publish latency
    /PhotonStreams/fps        we publish: measured pipeline rate

Setting enable=false in autonomousInit and true in disabledInit is the whole
integration.
"""
import argparse
import asyncio
import sys
import time

try:
    import msgpack
    import websockets
except ImportError:
    sys.exit("needs: pip install msgpack websockets")


async def _read_state(host):
    """Return (uniqueName, nickname, settings dict) for the first camera."""
    uri = "ws://%s:5800/websocket_data" % host
    async with websockets.connect(uri, max_size=None, open_timeout=10) as ws:
        for _ in range(60):
            msg = msgpack.unpackb(await asyncio.wait_for(ws.recv(), 10), raw=False)
            if not isinstance(msg, dict):
                continue
            cams = msg.get("cameraSettings") or msg.get("cameraSettingsList")
            if not cams:
                continue
            c = cams[0]
            return c.get("uniqueName"), c.get("nickname"), c.get("currentPipelineSettings", {})
    raise RuntimeError("no camera state from %s - is PhotonVision running?" % host)


async def _apply(host, unique_name, **kw):
    uri = "ws://%s:5800/websocket_data" % host
    async with websockets.connect(uri, max_size=None, open_timeout=10) as ws:
        kw["cameraUniqueName"] = unique_name
        await ws.send(msgpack.packb({"changePipelineSetting": kw}))
        await asyncio.sleep(1.5)


def describe(s):
    raw = s.get("inputShouldShow")
    proc = s.get("outputShouldShow")
    draw = s.get("outputShouldDraw")
    if not raw and not proc:
        return "OFF (no streams encoded - match mode)"
    parts = []
    if raw:
        parts.append("raw")
    if proc:
        parts.append("processed%s" % (" with overlays" if draw else ""))
    return "ON: " + " + ".join(parts)


def set_streams(host, raw, proc, draw=None, quiet=False):
    """Apply a stream configuration and verify it actually took."""
    uid, nick, before = asyncio.run(_read_state(host))
    if draw is None:
        draw = proc
    want = {"inputShouldShow": bool(raw), "outputShouldShow": bool(proc),
            "outputShouldDraw": bool(draw)}
    if all(before.get(k) == v for k, v in want.items()):
        if not quiet:
            print("%s: already %s" % (nick, describe(before)))
        return before
    asyncio.run(_apply(host, uid, **want))
    _, _, after = asyncio.run(_read_state(host))
    bad = [k for k, v in want.items() if after.get(k) != v]
    if not quiet:
        print("%s: %s  ->  %s" % (nick, describe(before), describe(after)))
        for k in bad:
            print("  !! %s did not take (wanted %s, got %s)" % (k, want[k], after.get(k)))
    if bad:
        raise RuntimeError("settings did not apply: %s" % ", ".join(bad))
    return after


def measure(host, camera, seconds=6.0):
    """Measure pipeline latency and rate over NetworkTables. Returns (fps, latency_ms)."""
    import ntcore
    from photonlibpy.photonCamera import PhotonCamera
    inst = ntcore.NetworkTableInstance.getDefault()
    if not inst.isConnected():
        inst.startClient4("set-streams-measure")
        inst.setServer(host, ntcore.NetworkTableInstance.kDefaultPort4)
        time.sleep(1.5)
    cam = PhotonCamera(camera)
    seen, lat = set(), []
    t0 = time.time()
    while time.time() - t0 < seconds:
        r = cam.getLatestResult()
        md = getattr(r, "metadata", None)
        if md and md.sequenceID not in seen:
            seen.add(md.sequenceID)
            lat.append((md.publishTimestampMicros - md.captureTimestampMicros) / 1000.0)
        time.sleep(0.002)
    if not lat:
        return float("nan"), float("nan")
    lat.sort()
    return len(seen) / seconds, lat[len(lat) // 2]


def daemon(host, table, nt_server, camera):
    """Watch an NT boolean and apply it; publish what we actually did."""
    import ntcore
    inst = ntcore.NetworkTableInstance.getDefault()
    inst.startClient4("photon-streams")
    inst.setServer(nt_server or host, ntcore.NetworkTableInstance.kDefaultPort4)
    t = inst.getTable(table)
    enable = t.getBooleanTopic("enable").getEntry(True)
    state = t.getBooleanTopic("state").publish()
    lat_pub = t.getDoubleTopic("latencyMs").publish()
    fps_pub = t.getDoubleTopic("fps").publish()
    enable.setDefault(True)

    uid, nick, cur = asyncio.run(_read_state(host))
    camera = camera or nick
    applied = bool(cur.get("outputShouldShow") or cur.get("inputShouldShow"))
    state.set(applied)
    print("daemon: %s, watching /%s/enable (currently %s)"
          % (nick, table, "on" if applied else "off"), flush=True)

    last_measure = 0.0
    while True:
        want = bool(enable.get(True))
        if want != applied:
            try:
                set_streams(host, raw=False, proc=want, draw=want, quiet=True)
                applied = want
                state.set(applied)
                print("%s  streams -> %s" % (time.strftime("%H:%M:%S"),
                                             "ON" if want else "OFF"), flush=True)
            except Exception as e:                       # keep the daemon alive
                print("  !! failed to apply: %s" % e, flush=True)
        if time.time() - last_measure > 10:
            last_measure = time.time()
            try:
                f, l = measure(host, camera, 3.0)
                fps_pub.set(float(f))
                lat_pub.set(float(l))
            except Exception:
                pass
        time.sleep(0.25)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--off", action="store_true", help="no streams at all (match)")
    g.add_argument("--on", action="store_true", help="processed stream with overlays (pit)")
    g.add_argument("--raw", action="store_true", help="raw stream only")
    g.add_argument("--both", action="store_true", help="raw + processed")
    g.add_argument("--status", action="store_true", help="show current state")
    g.add_argument("--daemon", action="store_true", help="follow NT, let the robot decide")
    ap.add_argument("--host", default="photonvision.local")
    ap.add_argument("--table", default="PhotonStreams", help="NT table for --daemon")
    ap.add_argument("--nt-server", default=None, help="NT server (default: --host)")
    ap.add_argument("--camera", default=None, help="camera nickname (default: auto)")
    ap.add_argument("--measure", action="store_true",
                    help="also measure latency/fps after applying")
    a = ap.parse_args()

    if a.daemon:
        return daemon(a.host, a.table, a.nt_server, a.camera)

    if a.status:
        uid, nick, s = asyncio.run(_read_state(a.host))
        print("%s: %s" % (nick, describe(s)))
        for k in ("inputShouldShow", "outputShouldShow", "outputShouldDraw",
                  "streamingFrameDivisor"):
            print("  %-22s %s" % (k, s.get(k)))
        if a.measure:
            f, l = measure(a.host, a.camera or nick)
            print("  %-22s %.1f fps, %.1f ms latency" % ("measured", f, l))
        return

    raw = a.raw or a.both
    proc = a.on or a.both
    set_streams(a.host, raw=raw, proc=proc)
    if a.measure:
        _, nick, _ = asyncio.run(_read_state(a.host))
        f, l = measure(a.host, a.camera or nick)
        print("  measured: %.1f fps, %.1f ms latency" % (f, l))


if __name__ == "__main__":
    main()
