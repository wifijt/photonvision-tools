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

With TWO cameras on one Pi the proportional gain is smaller, because the box is
already saturated, but it still helps - and it stacks with threads=1:

    2 cameras, threads=4, streams on    29.9 + 43.8 = 73.7 fps
    2 cameras, threads=1, streams off   47.7 + 46.8 = 94.5 fps

Measure this on a freshly restarted service; a long-running process drifts and
distorts the comparison.

The dashboard cannot do this - its stream selector requires at least one stream
to stay selected - so the only way to turn both off is the websocket API, which
is what this does.  It applies live, with no service restart.

    set_streams.py --status
    set_streams.py --off                  # match: no streams
    set_streams.py --on                   # pit: processed stream back
    set_streams.py --daemon               # let the robot decide, over NT

Every mode acts on ALL cameras unless you name one with --camera.

In daemon mode it watches a NetworkTables boolean and applies it, so the robot
can shed the streams itself when the match starts:

    /PhotonStreams/enable          robot writes: true = streams on, false = off
    /PhotonStreams/state           we publish: what is actually set right now
    /PhotonStreams/<camera>/fps        we publish: measured rate, per camera
    /PhotonStreams/<camera>/latencyMs  we publish: measured capture->publish

Setting enable=false in autonomousInit and true in disabledInit is the whole
integration.
"""
import argparse
import asyncio
import re
import sys
import time

try:
    import msgpack
    import websockets
except ImportError:
    sys.exit("needs: pip install msgpack websockets")


async def _read_all(host):
    """Return [(uniqueName, nickname, settings), ...] for EVERY camera."""
    uri = "ws://%s:5800/websocket_data" % host
    async with websockets.connect(uri, max_size=None, open_timeout=10) as ws:
        for _ in range(60):
            msg = msgpack.unpackb(await asyncio.wait_for(ws.recv(), 10), raw=False)
            if not isinstance(msg, dict):
                continue
            cams = msg.get("cameraSettings") or msg.get("cameraSettingsList")
            if not cams:
                continue
            return [(c.get("uniqueName"), c.get("nickname"),
                     c.get("currentPipelineSettings", {})) for c in cams]
    raise RuntimeError("no camera state from %s - is PhotonVision running?" % host)


async def _apply_many(host, targets, **kw):
    """One websocket, one changePipelineSetting per camera."""
    uri = "ws://%s:5800/websocket_data" % host
    async with websockets.connect(uri, max_size=None, open_timeout=10) as ws:
        for uid, _nick in targets:
            payload = dict(kw)
            payload["cameraUniqueName"] = uid
            await ws.send(msgpack.packb({"changePipelineSetting": payload}))
            await asyncio.sleep(1.0)
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


def _select(cams, only):
    if only is None:
        return cams
    hit = [c for c in cams if c[1] == only]
    if not hit:
        raise SystemExit("no camera named %r - have: %s"
                         % (only, ", ".join(repr(c[1]) for c in cams)))
    return hit


def set_streams(host, raw, proc, draw=None, quiet=False, only=None):
    """Apply to every camera (or just `only`), then verify it actually took."""
    cams = _select(asyncio.run(_read_all(host)), only)
    if draw is None:
        draw = proc
    want = {"inputShouldShow": bool(raw), "outputShouldShow": bool(proc),
            "outputShouldDraw": bool(draw)}
    todo = [(uid, nick) for uid, nick, s in cams
            if any(s.get(k) != v for k, v in want.items())]
    if not todo:
        if not quiet:
            for _uid, nick, s in cams:
                print("%s: already %s" % (nick, describe(s)))
        return
    before = {nick: s for _uid, nick, s in cams}
    asyncio.run(_apply_many(host, todo, **want))
    after = {nick: s for _uid, nick, s in _select(asyncio.run(_read_all(host)), only)}
    failed = []
    for _uid, nick in todo:
        bad = [k for k, v in want.items() if after.get(nick, {}).get(k) != v]
        if not quiet:
            print("%s: %s  ->  %s" % (nick, describe(before[nick]),
                                      describe(after.get(nick, {}))))
            for k in bad:
                print("  !! %s did not take (wanted %s, got %s)"
                      % (k, want[k], after.get(nick, {}).get(k)))
        failed += ["%s.%s" % (nick, k) for k in bad]
    if failed:
        raise RuntimeError("settings did not apply: %s" % ", ".join(failed))


def measure(host, cameras, seconds=6.0):
    """Measure rate and latency for each camera. Returns {nickname: (fps, ms)}."""
    import ntcore
    from photonlibpy.photonCamera import PhotonCamera
    inst = ntcore.NetworkTableInstance.getDefault()
    if not inst.isConnected():
        inst.startClient4("set-streams-measure")
        inst.setServer(host, ntcore.NetworkTableInstance.kDefaultPort4)
        time.sleep(1.5)
    cams = {n: PhotonCamera(n) for n in cameras}
    seen = {n: set() for n in cams}
    lat = {n: [] for n in cams}
    t0 = time.time()
    while time.time() - t0 < seconds:
        for n, c in cams.items():
            r = c.getLatestResult()
            md = getattr(r, "metadata", None)
            if md and md.sequenceID is not None and md.sequenceID not in seen[n]:
                seen[n].add(md.sequenceID)
                lat[n].append((md.publishTimestampMicros - md.captureTimestampMicros) / 1000.0)
        time.sleep(0.002)
    out = {}
    for n in cams:
        if lat[n]:
            lat[n].sort()
            out[n] = (len(seen[n]) / seconds, lat[n][len(lat[n]) // 2])
        else:
            out[n] = (float("nan"), float("nan"))
    return out


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "camera"


def daemon(host, table, nt_server, only):
    """Watch an NT boolean and apply it to every camera; publish what we did."""
    import ntcore
    inst = ntcore.NetworkTableInstance.getDefault()
    inst.startClient4("photon-streams")
    inst.setServer(nt_server or host, ntcore.NetworkTableInstance.kDefaultPort4)
    t = inst.getTable(table)
    enable = t.getBooleanTopic("enable").getEntry(True)
    state = t.getBooleanTopic("state").publish()
    enable.setDefault(True)

    cams = _select(asyncio.run(_read_all(host)), only)
    names = [nick for _uid, nick, _s in cams]
    pubs = {n: (t.getDoubleTopic("%s/fps" % _safe(n)).publish(),
                t.getDoubleTopic("%s/latencyMs" % _safe(n)).publish()) for n in names}
    applied = any(s.get("outputShouldShow") or s.get("inputShouldShow")
                  for _uid, _n, s in cams)
    state.set(applied)
    print("daemon: %s, watching /%s/enable (currently %s)"
          % (", ".join(names), table, "on" if applied else "off"), flush=True)

    last = 0.0
    while True:
        want = bool(enable.get(True))
        if want != applied:
            try:
                set_streams(host, raw=False, proc=want, draw=want, quiet=True, only=only)
                applied = want
                state.set(applied)
                print("%s  streams -> %s" % (time.strftime("%H:%M:%S"),
                                             "ON" if want else "OFF"), flush=True)
            except Exception as e:                       # keep the daemon alive
                print("  !! failed to apply: %s" % e, flush=True)
        if time.time() - last > 10:
            last = time.time()
            try:
                for n, (f, l) in measure(host, names, 3.0).items():
                    pubs[n][0].set(float(f))
                    pubs[n][1].set(float(l))
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
    ap.add_argument("--camera", default=None,
                    help="act on this camera only (default: all of them)")
    ap.add_argument("--measure", action="store_true",
                    help="also measure latency/fps after applying")
    a = ap.parse_args()

    if a.daemon:
        return daemon(a.host, a.table, a.nt_server, a.camera)

    cams = _select(asyncio.run(_read_all(a.host)), a.camera)

    if a.status:
        for _uid, nick, s in cams:
            print("%s: %s" % (nick, describe(s)))
            for k in ("inputShouldShow", "outputShouldShow", "outputShouldDraw",
                      "streamingFrameDivisor", "threads"):
                print("  %-22s %s" % (k, s.get(k)))
    else:
        set_streams(a.host, raw=a.raw or a.both, proc=a.on or a.both, only=a.camera)

    if a.measure:
        for n, (f, l) in measure(a.host, [c[1] for c in cams]).items():
            print("  %-16s %.1f fps, %.1f ms latency" % (n, f, l))


if __name__ == "__main__":
    main()
