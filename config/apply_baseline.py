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
"""Apply a known-good AprilTag pipeline baseline, live, over the websocket.

These are the STRUCTURAL settings - the ones that do not depend on lighting.
photontune deliberately does not touch them: it tunes exposure, which is a
venue-dependent value, while everything here should be the same everywhere.

Every value below has a measured reason, given in WHY. Run with --explain to
print them without changing anything.
"""
import argparse, asyncio, sys, time

try:
    import msgpack, websockets
except ImportError:
    sys.exit("need: pip install msgpack websockets")

BASELINE = {
    "decimate": (2, """Search-stage downsampling. Costs RANGE, not accuracy - corner
        refinement always happens at full resolution. Measured: identical reprojection
        at 1/2/3/4 (0.11-0.14 px) but detection range falls roughly 18m / 9m / 6m / 4.6m.
        2 keeps ~9m for tags you care about at 5-6m. Do not raise it to buy framerate."""),
    "numIterations": (40, """AprilTag pose refinement iterations. The default. Was found
        at 222 on the reference rig, which cost CPU for no measurable accuracy gain."""),
    "threads": (1, """Detector worker threads, PER CAMERA. Matching physical cores sounds
        right and is wrong: the detector's own threading competes with capture, streaming
        and the JVM, and on a saturated Pi 5 more threads cost more than they return.
        Measured, one camera: threads=4 detected in 15.58 ms, threads=1 in 14.90 ms.
        With two cameras it is decisive, because each camera gets its own pool - 4 each
        is 8 threads on 4 cores:
            threads=4 each   29.9 + 43.8 = 73.7 fps total
            threads=2 each   32.9 + 47.1 = 80.0
            threads=1 each   31.3 + 52.6 = 84.0   and ~20 ms less latency on both
        Raise it only if you measure a gain on your own hardware."""),
    "decisionMargin": (35, """Detection confidence floor. 50 was rejecting valid tags -
        lowering it to 35 recovered a tag that had never been detected at all.
        Lower admits false positives; multi-tag rejects those anyway."""),
    "doMultiTarget": (True, """Multi-tag PnP. THE fix for pose flipping. Single-tag PnP on
        a face-on tag is genuinely ambiguous - measured 96% of detections above the 0.2
        reject threshold, with one tag reporting 0.33m when it was really 2.4m.
        Requires a loaded AprilTagFieldLayout."""),
    "solvePNPEnabled": (True, """3D pose output. NOTE: with no calibration for the active
        resolution this throws every frame and emits NOTHING - not a 2D fallback.
        Turn it off before switching to an uncalibrated resolution."""),
    "inputImageRotationMode": ("DEG_0", """MUST stay DEG_0. Non-zero rotation corrupts the
        multi-tag pose estimate by ~0.4m while the published corners stay correct - so it
        fails silently. See PhotonVision issue #2613. If the camera is mounted sideways,
        accept a sideways preview."""),
    "cameraAutoExposure": (False, """Auto-exposure optimises for the whole scene, not the
        tags, and drifts. Set exposure explicitly with photontune."""),
    "cameraBrightness": (40, """Additive image offset. Low values CRUSH the image to black,
        and nothing downstream recovers it - an exposure tuner will just compensate with a
        long, blurry exposure and report success. If the picture is black in a lit room,
        check this before anything else. 40 is the reference-rig value; adjust with
        --brightness if your camera differs."""),
    "blur": (0.0, """Gaussian blur applied BEFORE detection. Any non-zero value softens the
        tag edges the detector depends on. At 3.5 detection stops entirely and NO amount of
        exposure or gain recovers it - an exposure tuner will sweep its whole range, fail,
        and correctly tell you it is not an exposure problem. Keep at 0."""),
    "refineEdges": (True, """Sub-pixel corner refinement. Off, corners land on whole pixels
        and pose precision collapses - this is the step that makes decimate free, since
        refinement always runs at full resolution regardless of the search scale."""),
    "targetModel": ("kAprilTag6p5in_36h11", """Physical tag size. Sets the SCALE of every
        distance you measure. Wrong here and all ranges are proportionally wrong while
        looking perfectly self-consistent. 6.5in = 165.1mm is the FRC standard."""),
    "tagFamily": ("kTag36h11", """FRC uses 36h11. Wrong family = no detections at all."""),
    "cameraRedGain": (0, """Inert on a monochrome sensor (OV9281 etc - there are no colour
        channels). Pinned so a stray UI edit cannot affect a colour camera."""),
    "cameraBlueGain": (0, """See cameraRedGain."""),
}

NOT_SET_HERE = """
  cameraExposureRaw / cameraGain   venue-dependent - use photontune
  cameraVideoModeIndex             changing resolution invalidates the calibration
  pipelineNickname                 name your profiles for WHEN to use them
"""


# PhotonVision reports enums as ints at runtime but stores them as names.
ENUM_ALIASES = {
    "inputImageRotationMode": {"DEG_0": (0, "DEG_0"), "DEG_90_CCW": (1, "DEG_90_CCW"),
                               "DEG_180_CCW": (2, "DEG_180_CCW"), "DEG_270_CCW": (3, "DEG_270_CCW")},
    "targetModel": {"kAprilTag6p5in_36h11": (7, "kAprilTag6p5in_36h11")},
    "tagFamily": {"kTag36h11": (0, "kTag36h11")},
}


def _matches(have, want, key=None):
    if key and key in ENUM_ALIASES and want in ENUM_ALIASES[key]:
        return have in ENUM_ALIASES[key][want]
    if isinstance(want, bool):
        return bool(have) == want
    if isinstance(want, (int, float)):
        try:
            return abs(float(have) - float(want)) < 1e-9
        except (TypeError, ValueError):
            return False
    return have == want


async def apply(host, camera_filter, dry_run):
    uri = "ws://%s:5800/websocket_data" % host
    try:
        ws = await websockets.connect(uri, open_timeout=10, max_size=80_000_000)
    except Exception as exc:
        sys.exit("could not reach PhotonVision at %s (%s)" % (uri, type(exc).__name__))
    async with ws:
        cams, t0 = {}, time.time()
        while time.time() - t0 < 12 and not cams:
            raw = await asyncio.wait_for(ws.recv(), timeout=8)
            if not isinstance(raw, bytes):
                continue
            msg = msgpack.unpackb(raw, raw=False)
            if isinstance(msg, dict):
                for c in msg.get("cameraSettings", []) or []:
                    cams[c["uniqueName"]] = c
        if not cams:
            sys.exit("no cameras reported by PhotonVision at %s" % host)

        for unique, cam in cams.items():
            nick = cam.get("nickname", unique)
            if camera_filter and nick.lower() not in camera_filter:
                continue
            cur = cam["currentPipelineSettings"]
            print("── %s  (pipeline %s '%s')" % (nick, cur["pipelineIndex"], cur["pipelineNickname"]))
            changes = {}
            for key, (want, _why) in BASELINE.items():
                have = cur.get(key)
                same = _matches(have, want, key)
                if same:
                    print("     %-24s %-10s ok" % (key, have))
                else:
                    print("     %-24s %-10s -> %s" % (key, have, want))
                    changes[key] = want
            if not changes:
                print("     already at baseline")
                continue
            if dry_run:
                print("     dry run - nothing changed")
                continue
            for key, val in changes.items():
                await ws.send(msgpack.packb(
                    {"changePipelineSetting": {key: val, "cameraUniqueName": unique}}))
                await asyncio.sleep(0.35)
            print("     applied %d change(s)" % len(changes))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="photonvision.local")
    p.add_argument("--cameras", default=None, help="comma-separated nicknames")
    p.add_argument("--brightness", type=float, default=None,
                   help="override the baseline cameraBrightness for your camera")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--explain", action="store_true", help="print the reasoning and exit")
    a = p.parse_args()
    if a.explain:
        for k, (v, why) in BASELINE.items():
            print("%s = %s" % (k, v))
            print("    " + " ".join(why.split()) + "\n")
        print("Deliberately NOT set here:" + NOT_SET_HERE)
        return
    if a.brightness is not None:
        BASELINE["cameraBrightness"] = (a.brightness, BASELINE["cameraBrightness"][1])
    filt = {c.strip().lower() for c in a.cameras.split(",")} if a.cameras else None
    asyncio.run(apply(a.host, filt, a.dry_run))


if __name__ == "__main__":
    main()
