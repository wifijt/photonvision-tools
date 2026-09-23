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
"""Notice when a camera moves, using the other camera as the reference.

Two cameras bolted to one rig have a FIXED transform between them.  Neither one
has to be right about the field for that to be true, so this needs no survey, no
accurate layout, no robot and no calibration - only that the bolts have not
moved.  When the measured transform between the two changes by more than the
noise floor, something moved.

    python3 camera_drift.py --host photonvision.local --baseline rig.json
    ... later, or continuously ...
    python3 camera_drift.py --host photonvision.local --watch rig.json

WHY THIS ONE IS WORTH BUILDING WHEN THE REST WAS NOT
    A camera that shifts on its mount is a confirmed competition failure, it is
    silent, and there is no published way to notice it.  It also survives the
    fix everyone sensibly adopted: teams gave up on global pose and now align
    relative to the single tag they are scoring on - but a wrong robot-to-camera
    transform corrupts that just as badly, because it sits between the tag and
    every use of the tag.

    And it is self-checking in a way nothing else here is: two independent
    estimates of one rigid quantity.  If they disagree, the disagreement is
    real.  There is no model to be wrong about.

WHAT IT CANNOT DO
    The websocket carries no capture timestamp, so samples from the two cameras
    are paired by arrival.  That is fine standing still and wrong while moving
    fast, so the monitor only scores frames while the rig is STILL, which is
    also the only time a drift check means anything.  It cannot tell you WHICH
    camera moved - only that the pair no longer agrees.  Two stations at
    different distances will usually settle that by inspection.
"""
import argparse, asyncio, json, math, os, statistics as st, sys, time
from collections import defaultdict, deque

try:
    import msgpack, websockets, numpy as np
except ImportError:
    sys.exit("need: pip install msgpack websockets numpy")

PAIR_WINDOW_S = 0.15   # how close in arrival two samples must be to be a pair
STILL_MM      = 8.0    # rig counts as still if it moved less than this ...
STILL_N       = 12     # ... across this many recent samples
MIN_PAIRS     = 60     # a baseline with fewer is not a baseline


def T(bt):
    """4x4 from PhotonVision's websocket transform dict."""
    w, x, y, z = bt["qw"], bt["qx"], bt["qy"], bt["qz"]
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    M = np.eye(4)
    M[:3, :3] = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z),     2 * (x * z + w * y)],
        [2 * (x * y + w * z),     1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y),     2 * (y * z + w * x),     1 - 2 * (x * x + y * y)]])
    M[:3, 3] = [bt["x"], bt["y"], bt["z"]]
    return M


def rot_angle(R):
    """Magnitude of a rotation matrix, in degrees."""
    c = max(-1.0, min(1.0, (np.trace(R[:3, :3]) - 1.0) / 2.0))
    return math.degrees(math.acos(c))


def pose_of(res):
    mt = res.get("multitagResult")
    if not mt or not mt.get("bestTransform"):
        return None, 0
    used = mt.get("fiducialIDsUsed") or []
    return T(mt["bestTransform"]), len(used)


async def gather(host, port, seconds, label):
    """Collect paired cam-to-cam transforms while the rig is still."""
    uri = "ws://%s:%d/websocket_data" % (host, port)
    try:
        ws = await websockets.connect(uri, open_timeout=10, max_size=80_000_000)
    except Exception as exc:
        sys.exit("could not reach PhotonVision at %s (%s)" % (uri, type(exc).__name__))

    names, latest, recent = {}, {}, defaultdict(lambda: deque(maxlen=STILL_N))
    pairs, seen = [], defaultdict(set)
    skipped_moving = 0
    t_end = time.time() + seconds
    print("%s for %d s ..." % (label, seconds))
    async with ws:
        while time.time() < t_end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue
            if not isinstance(raw, bytes):
                continue
            msg = msgpack.unpackb(raw, raw=False)
            if not isinstance(msg, dict):
                continue
            for c in msg.get("cameraSettings", []) or []:
                names[c["uniqueName"]] = c.get("nickname", c["uniqueName"])
            upr = msg.get("updatePipelineResult")
            if not upr:
                continue
            now = time.time()
            for uid, res in upr.items():
                sid = res.get("sequenceID")
                if sid in seen[uid]:
                    continue
                seen[uid].add(sid)
                if len(seen[uid]) > 6000:
                    seen[uid] = set(list(seen[uid])[-2000:])
                M, ntag = pose_of(res)
                if M is None:
                    continue
                latest[uid] = (now, M, ntag)
                recent[uid].append(M[:3, 3].copy())

            if len(latest) < 2:
                continue
            uids = sorted(latest, key=lambda u: names.get(u, u))
            if len(uids) != 2:
                continue
            a, b = uids
            (ta, Ma, na), (tb, Mb, nb) = latest[a], latest[b]
            if abs(ta - tb) > PAIR_WINDOW_S:
                continue
            still = True
            for u in (a, b):
                r = recent[u]
                if len(r) < STILL_N:
                    still = False
                    break
                span = max(np.linalg.norm(p - q) for p in r for q in r)
                if span * 1000.0 > STILL_MM:
                    still = False
                    break
            if not still:
                skipped_moving += 1
                continue
            rel = np.linalg.inv(Ma) @ Mb
            pairs.append({"t": now, "d": float(np.linalg.norm(rel[:3, 3])),
                          "ang": rot_angle(rel), "rel": rel.tolist(),
                          "ntags": [na, nb]})
    return names, uids if len(latest) >= 2 else [], pairs, skipped_moving


def describe(pairs):
    d = [p["d"] for p in pairs]
    a = [p["ang"] for p in pairs]
    return {"n": len(pairs),
            "dist_m": st.median(d), "dist_sd_mm": st.stdev(d) * 1000 if len(d) > 1 else 0.0,
            "ang_deg": st.median(a), "ang_sd_deg": st.stdev(a) if len(a) > 1 else 0.0}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="photonvision.local")
    p.add_argument("--port", type=int, default=5800)
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--baseline", metavar="FILE", help="record the rig as it is now")
    p.add_argument("--watch", metavar="FILE", help="compare against a recorded baseline")
    p.add_argument("--sigma", type=float, default=5.0,
                   help="alarm at this many baseline sigmas (default 5)")
    a = p.parse_args()
    if not a.baseline and not a.watch:
        sys.exit("give --baseline FILE to record, or --watch FILE to compare")
    if a.baseline and a.watch:
        sys.exit("--baseline and --watch are different jobs; pick one")

    names, uids, pairs, moving = asyncio.run(
        gather(a.host, a.port, a.seconds, "recording" if a.baseline else "watching"))
    nicks = [names.get(u, u[:8]) for u in uids]

    if len(pairs) < MIN_PAIRS:
        sys.exit("\nonly %d usable pairs (needed %d). Both cameras must hold a "
                 "multi-tag solve at the same time, and the rig must be still - "
                 "%d frames were skipped as moving." % (len(pairs), MIN_PAIRS, moving))

    cur = describe(pairs)
    print("\n%s  <->  %s" % (nicks[0], nicks[1]))
    print("  pairs %d   (%d frames skipped as moving)" % (cur["n"], moving))
    print("  separation  %.1f mm  +/- %.2f" % (cur["dist_m"] * 1000, cur["dist_sd_mm"]))
    print("  angle       %.3f deg +/- %.3f" % (cur["ang_deg"], cur["ang_sd_deg"]))

    if a.baseline:
        with open(a.baseline, "w") as f:
            json.dump({"recorded": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "cameras": nicks, "summary": cur}, f, indent=2)
        print("\nwrote %s" % a.baseline)
        print("This is the reference. It assumes the rig is CORRECT right now -")
        print("it records where things are, not whether they are right.")
        return

    if not os.path.exists(a.watch):
        sys.exit("no such baseline: %s" % a.watch)
    with open(a.watch) as f:
        base = json.load(f)
    b = base["summary"]
    if base.get("cameras") != nicks:
        print("\nWARNING: baseline was %s, now %s" % (base.get("cameras"), nicks))

    dd = abs(cur["dist_m"] - b["dist_m"]) * 1000.0
    da = abs(cur["ang_deg"] - b["ang_deg"])
    # noise floor is the baseline's own scatter, never zero
    td = max(b["dist_sd_mm"], 0.05) * a.sigma
    ta = max(b["ang_sd_deg"], 0.002) * a.sigma
    print("\n  vs baseline recorded %s" % base["recorded"])
    print("  separation changed  %6.2f mm   threshold %5.2f   %s"
          % (dd, td, "** MOVED **" if dd > td else "ok"))
    print("  angle changed       %6.3f deg  threshold %5.3f   %s"
          % (da, ta, "** MOVED **" if da > ta else "ok"))
    if dd > td or da > ta:
        print("\n  Something on the rig moved. This cannot say WHICH camera -")
        print("  only that the pair no longer agrees. Check both mounts, and")
        print("  re-record a baseline once you are satisfied it is right.")
        sys.exit(1)
    print("\n  Both mounts are where they were.")


if __name__ == "__main__":
    main()
