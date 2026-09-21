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
"""Guided ChArUco capture: prompts where to put the board, then shoots it.

    # watch the mirrored view first, it is much easier to aim with
    python3 ../calib/calib_view.py --host photonvision.local &
    open http://localhost:8078

    python3 charuco_capture.py --camera "OV9281" --dry-run     # rehearse
    python3 charuco_capture.py --camera "OV9281"               # for real

WHAT IT IS FOR

PhotonVision's own calibration UI is a Take-Snapshot button. Clicking it 120
times while remembering which parts of the frame you have already covered is
the actual difficulty, and the thing people get wrong is coverage: a
calibration shot only from the middle of the frame produces intrinsics that are
confident in the middle and wrong at the edges - which is exactly where a tag
sits when a robot is turning. This walks a coverage plan and shoots on a timer
so your hands stay on the board.

IT REPORTS WHAT PHOTONVISION KEPT, NOT WHAT YOU SHOT

Those are different numbers and only the first one counts. A snapshot where the
board was not fully found is discarded silently, so "I took 120" routinely
means "it has 80".

*** THIS REPLACES THE CAMERA'S EXISTING CALIBRATION ***

--end computes and stores a new one. Until you pass --end, nothing is
overwritten: you can abandon a bad session by simply not finishing it, and the
old calibration stays. --dry-run walks the whole plan, prompts, and takes no
snapshots at all, which is the right way to rehearse the choreography.

ON THE MIRRORED VIEW

Use calib_view.py while you do this. You stand facing the camera, so moving the
board to YOUR left puts it on the RIGHT of the image, and every instruction
below is in IMAGE coordinates. The mirrored view makes the board move the way
your hands do. The mirror is display-only; the frames PhotonVision calibrates
from are untouched.
"""
import argparse, asyncio, json, sys, time, urllib.request

try:
    import msgpack, websockets
except ImportError:
    sys.exit("need: pip install msgpack websockets")

BOARD_CHARUCO = 1                      # CalibrationBoardTypes.Charuco
TAG_FAMILIES = {"4x4": 0, "5x5": 1, "6x6": 2, "7x7": 3}   # CalibrationTagFamilies

# Coverage plan. Order matters: centre first so a mistake in board size or
# family shows up immediately, corners next because they are what people miss,
# distance last because it is the tiring part.
PLAN = [
    ("CENTRE - tilt it about: lean the top away, then the bottom, then each side", 10),
    ("UPPER LEFT of the image", 8),
    ("UPPER RIGHT of the image", 8),
    ("LOWER RIGHT of the image", 8),
    ("LOWER LEFT of the image", 8),
    ("TOP EDGE - slide along it, board half out of frame is fine", 8),
    ("BOTTOM EDGE - slide along it", 8),
    ("LEFT EDGE - slide up and down", 8),
    ("RIGHT EDGE - slide up and down", 8),
    ("FAR BACK - board small, move it around the WHOLE frame", 10),
    ("CLOSE IN - board filling the frame, strong tilts", 10),
    ("ANYWHERE - fill in whatever felt thin", 10),
]


class PV:
    def __init__(self, host, port=5800):
        self.host, self.port = host, port
        self.uri = "ws://%s:%d/websocket_data" % (host, port)
        self.ws = None

    async def __aenter__(self):
        self.ws = await websockets.connect(self.uri, open_timeout=15,
                                           ping_interval=None, max_size=80_000_000)
        return self

    async def __aexit__(self, *a):
        if self.ws:
            await self.ws.close()

    async def cameras(self, timeout=15):
        found, t0 = {}, time.time()
        while time.time() - t0 < timeout and not found:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
            if not isinstance(raw, bytes):
                continue
            m = msgpack.unpackb(raw, raw=False)
            if isinstance(m, dict):
                for c in m.get("cameraSettings", []) or []:
                    found[c["uniqueName"]] = c
        return found

    async def send(self, payload):
        await self.ws.send(msgpack.packb(payload))

    async def kept(self, timeout=4.0):
        """How many observations PhotonVision has KEPT, from its own state."""
        t0 = time.time()
        best = None
        while time.time() - t0 < timeout:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                break
            if not isinstance(raw, bytes):
                continue
            m = msgpack.unpackb(raw, raw=False)
            if not isinstance(m, dict):
                continue
            cd = m.get("calibrationData")
            if isinstance(cd, dict):
                best = cd
        return best


def end_calibration(host, port, unique):
    """POST /api/calibration/end - this is what writes the new calibration."""
    req = urllib.request.Request(
        "http://%s:%d/api/calibration/end" % (host, port),
        data=json.dumps({"cameraUniqueName": unique}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read().decode(errors="replace")[:400]


async def run(a):
    async with PV(a.host, a.port) as pv:
        cams = await pv.cameras()
        if not cams:
            sys.exit("no cameras reported by PhotonVision at %s" % a.host)
        match = [(u, c) for u, c in cams.items()
                 if c.get("nickname") == a.camera or u == a.camera]
        if not match:
            sys.exit("no camera %r. Available: %s"
                     % (a.camera, ", ".join(c.get("nickname", "?") for c in cams.values())))
        unique, cam = match[0]
        st = cam["currentPipelineSettings"]
        fmts = cam.get("videoFormatList") or {}
        mode = a.mode if a.mode is not None else st.get("cameraVideoModeIndex", 0)
        f = fmts.get(mode) or fmts.get(str(mode)) or {}
        cals = [((x.get("resolution") or {}).get("width"),
                 (x.get("resolution") or {}).get("height"))
                for x in (cam.get("calibrations") or [])]

        print("camera      : %s" % cam.get("nickname"))
        print("video mode  : %s  ->  %sx%s" % (mode, f.get("width"), f.get("height")))
        print("board       : %dx%d, square %.3f in, marker %.3f in, %s"
              % (a.width, a.height, a.square, a.marker, a.family))
        print("already calibrated at: %s"
              % (", ".join("%sx%s" % r for r in cals) if cals else "nothing"))
        if (f.get("width"), f.get("height")) in cals:
            print("\n*** THIS RESOLUTION IS ALREADY CALIBRATED ***")
            print("    Finishing with --end REPLACES it. Abandon the session")
            print("    without --end and the existing one survives untouched.")
        total = sum(n for _, n in PLAN)
        print("\nplan: %d prompts, %d shots, about %.0f s"
              % (len(PLAN), total, total * a.interval + len(PLAN) * a.settle))
        if a.dry_run:
            print("DRY RUN - no snapshots, nothing changed\n")
        else:
            print("Watch the mirrored view: http://localhost:8078\n")
            input("board ready? press Enter to start, Ctrl-C to abandon ")

        if not a.dry_run:
            await pv.send({"startCalibration": {
                "cameraUniqueName": unique,
                "videoModeIndex": int(mode),
                "squareSizeIn": float(a.square),
                "markerSizeIn": float(a.marker),
                "patternWidth": int(a.width),
                "patternHeight": int(a.height),
                "boardType": BOARD_CHARUCO,
                "useOldPattern": False,
                "tagFamily": TAG_FAMILIES[a.family],
            }})
            await asyncio.sleep(2.0)

        shot = 0
        try:
            for phase, n in PLAN:
                print("\n>>> %s   (%d shots)" % (phase, n))
                for s in range(a.settle, 0, -1):
                    print("    starting in %d ..." % s, end="\r", flush=True)
                    await asyncio.sleep(1.0)
                for i in range(n):
                    if not a.dry_run:
                        await pv.send({"takeCalibrationSnapshot": {
                            "cameraUniqueName": unique}})
                    shot += 1
                    cd = None if a.dry_run else await pv.kept(timeout=a.interval)
                    k = (cd or {}).get("count")
                    print("    %2d/%-2d   shot %3d   kept %s        "
                          % (i + 1, n, shot,
                             k if k is not None else ("-" if a.dry_run else "?")),
                          end="\r", flush=True)
                    if a.dry_run:
                        await asyncio.sleep(min(a.interval, 0.25))
                print()
        except KeyboardInterrupt:
            print("\n\nabandoned after %d shots. Nothing was written - the old "
                  "calibration is intact." % shot)
            return 1

        cd = None if a.dry_run else await pv.kept(timeout=5.0)
        kept = (cd or {}).get("count")
        print("\n%d shots taken, PhotonVision kept %s"
              % (shot, kept if kept is not None else "unknown"))
        if kept is not None and kept < shot * 0.6:
            print("  Fewer than 60%% were usable. Usually the board was not fully")
            print("  in frame, or it is being read as the wrong size/family.")
        if a.dry_run:
            print("\ndry run complete - nothing was captured or changed")
            return 0
        if not a.end:
            print("\nNOT finishing: pass --end to compute and STORE the")
            print("calibration. Until then the old one is untouched.")
            return 0
        print("\ncomputing (this takes a while, and replaces the old calibration)...")
        code, body = end_calibration(a.host, a.port, unique)
        print("  /api/calibration/end -> HTTP %s  %s" % (code, body))
        return 0 if code == 200 else 1


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="photonvision.local")
    p.add_argument("--port", type=int, default=5800)
    p.add_argument("--camera", required=True, help="nickname, e.g. \"OV9281\"")
    p.add_argument("--mode", type=int, default=None,
                   help="video mode index (default: whatever is active)")
    p.add_argument("--width", type=int, default=8, help="board squares across")
    p.add_argument("--height", type=int, default=8, help="board squares down")
    p.add_argument("--square", type=float, default=1.0, help="square size, inches")
    p.add_argument("--marker", type=float, default=0.75, help="marker size, inches")
    p.add_argument("--family", default="4x4", choices=sorted(TAG_FAMILIES),
                   help="ArUco dictionary on your printed board")
    p.add_argument("--interval", type=float, default=1.2, help="seconds between shots")
    p.add_argument("--settle", type=int, default=4, help="seconds to reposition between phases")
    p.add_argument("--dry-run", action="store_true",
                   help="walk the plan, take nothing, change nothing")
    p.add_argument("--end", action="store_true",
                   help="compute and STORE the calibration, REPLACING the existing one")
    a = p.parse_args()
    try:
        sys.exit(asyncio.run(run(a)))
    except KeyboardInterrupt:
        print("\nabandoned - nothing written")
        sys.exit(1)


if __name__ == "__main__":
    main()
