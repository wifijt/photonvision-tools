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
"""Measure where the cameras are, by spinning instead of by tape measure.

Rotate the rig about a fixed vertical axis with tags in view.  Every camera then
traces a circle about that same axis, so fitting all the circles TOGETHER against
one shared centre recovers each camera's radius and bearing at once.

Why not a ruler: the thing you need is the optical centre inside the lens, which
you cannot reach; the mounting ANGLES matter more than the position and are
nearly impossible to measure by hand; and any error there biases every pose
estimate systematically, so it never averages out.

Sharing the centre is what makes it work with several cameras: a camera that only
glimpses tags for a short arc still solves, because the centre is pinned by the
cameras that saw a long one.

    calibrate_mount.py record spin.npz 60
    calibrate_mount.py solve  spin.npz

BENCH USE (no robot): pivot the rig by hand about a fixed point - a mug, a lazy
susan - keeping tags in view.  Check the reported radius against a ruler from the
pivot to each lens.  The camera-to-camera yaw it reports should also match what
you get from the two cameras' simultaneous poses.

ROBOT USE: spin in place about two full turns, slowly.  The rotation centre of a
swerve IS the point robotToCamera should reference.  Absolute "robot forward"
needs one extra straight drive; this tool gives you everything else.

WHAT NEEDS AN ANCHOR, AND WHAT DOES NOT
  radius, scale, relative bearing/yaw   no anchor - they are differences, and
                                        scale comes from the known tag size
  Z (camera height)                     YES - pass --origin-height. A surveyed
                                        bench layout anchors Z to a TAG, not the
                                        floor. The official FRC field layout
                                        already carries true heights (pass 0).
  "robot forward"                       YES - needs one straight drive. Not
                                        available, and not meaningful, on a bench.

VALIDATED 2026-09-14 on a two-camera bench rig, hand-pivoted about a fixed point:
  - synthetic ground truth recovered exactly (centre, radii, yaw)
  - yaw between cameras -30.4 deg, against -29.0/-29.3 from an unrelated method
    (differencing the two cameras' simultaneous multi-tag poses)
  - bearing separation 7.05 deg vs 7.28 predicted from the chord and radius
  - camera 1 radius 330.2 mm vs a 342.9 mm tape to the LENS FRONT, implying a
    12.8 mm entrance-pupil offset inside a 21 mm barrel - physically right
  - camera 2's tape implied a 22.7 mm offset, i.e. an optical centre BEHIND the
    sensor. Impossible, so that tape reading was wrong, not the fit. Worth
    knowing: a physical constraint (barrel length) settled what 2500 frames
    could not.

ACCURACY. On a bench expect ~+/-5-10 mm on position and ~1 deg on angles. The
bootstrap will claim far better - it measures PRECISION, not accuracy, and these
pose estimates carry a viewpoint-dependent bias of roughly +/-10 mm that no
amount of averaging at one viewpoint removes. A robot spinning two full turns
improves all of it: 360 deg of arc instead of 55-80, a genuinely fixed axis
instead of your hand, and a larger radius.
"""
import argparse
import math
import sys
import time

import numpy as np

PER_BIN = 12          # max samples per 2-degree slice of the arc


def q2R(w, x, y, z):
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def record(path, seconds, host, names):
    import ntcore
    from photonlibpy.photonCamera import PhotonCamera
    inst = ntcore.NetworkTableInstance.getDefault()
    inst.startClient4("mount-cal")
    inst.setServer(host, ntcore.NetworkTableInstance.kDefaultPort4)
    cams = {n: PhotonCamera(n) for n in names}
    time.sleep(2.5)
    rows = {n: [] for n in names}
    seen = {n: set() for n in names}
    print("RECORDING %.0fs - rotate the rig about a fixed point now" % seconds, flush=True)
    t0 = time.time(); last = 0
    while time.time() - t0 < seconds:
        for n, c in cams.items():
            r = c.getLatestResult()
            md = getattr(r, "metadata", None)
            if not md or md.sequenceID in seen[n]:
                continue
            seen[n].add(md.sequenceID)
            mt = getattr(r, "multitagResult", None)
            if mt is not None and hasattr(mt, "isPresent"):
                mt = mt.get() if mt.isPresent() else None
            if mt is None or getattr(mt, "estimatedPose", None) is None:
                continue
            b = mt.estimatedPose.best
            q = b.rotation().getQuaternion()
            R = q2R(q.W(), q.X(), q.Y(), q.Z())
            rows[n].append([md.captureTimestampMicros / 1e6, b.X(), b.Y(), b.Z(),
                            math.atan2(R[1, 0], R[0, 0])])
        if time.time() - t0 - last > 10:
            last = time.time() - t0
            print("  %3ds  %s" % (last, "  ".join("%s=%d" % (n, len(rows[n])) for n in names)),
                  flush=True)
        time.sleep(0.002)
    np.savez(path, **{n: np.array(v) for n, v in rows.items()}, names=np.array(names))
    print("\nwrote %s" % path)
    for n in names:
        print("  %-14s %d solves" % (n, len(rows[n])))


def solve(path, origin_height=None):
    d = np.load(path, allow_pickle=True)
    names = [str(x) for x in d["names"]]
    data = {n: d[n] for n in names if d[n].size}
    for n in names:
        if n not in data:
            print("!! %s has no multi-tag solves - excluded" % n)
    names = [n for n in names if n in data]
    if not names:
        sys.exit("no usable data")

    # --- did the rig actually rotate? a pure translation cannot be fitted ---
    print("%-16s %7s %10s %12s" % ("camera", "n", "arc swept", "yaw range"))
    arcs = {}
    for n in names:
        yaw = np.unwrap(data[n][:, 4])
        arcs[n] = math.degrees(yaw.max() - yaw.min())
        print("%-16s %7d %9.1f deg %11.1f deg"
              % (n, len(data[n]), arcs[n], math.degrees(np.ptp(data[n][:, 4]))))
    if max(arcs.values()) < 40:
        print("\n!! largest arc is only %.0f deg. Rotate FURTHER - under ~40 deg the"
              " circle fit is ill-conditioned and the radius is meaningless." % max(arcs.values()))

    # --- even out the arc before fitting -------------------------------------
    # Least squares weights by sample COUNT, so anywhere you paused the rig drags
    # the fit towards that spot. Measured: leaving a 34 s stationary tail in a 60 s
    # run moved the radius 4% (344.0 -> 330.5 mm) and made the two cameras
    # disagree by 2.4 mm instead of 0.5. Bin by angle and cap each bin, so a
    # slow sweep and a pause contribute the same as a fast one.
    binned = {}
    for n in names:
        p = data[n]
        yaw = np.unwrap(p[:, 4])
        nb = max(8, int(math.degrees(yaw.max() - yaw.min()) / 2.0))
        which = np.clip(((yaw - yaw.min()) / max(yaw.ptp() if hasattr(yaw, "ptp") else np.ptp(yaw), 1e-9)
                         * (nb - 1)).astype(int), 0, nb - 1)
        keep = []
        for b in range(nb):
            hit = np.flatnonzero(which == b)
            if hit.size:
                keep.extend(hit[:: max(1, hit.size // PER_BIN)][:PER_BIN])
        binned[n] = p[np.sort(np.array(keep))]
    dropped = sum(len(data[n]) - len(binned[n]) for n in names)
    if dropped:
        print("\nevened the arc: %d of %d samples dropped where the rig lingered"
              % (dropped, sum(len(data[n]) for n in names)))
    data = binned

    # --- joint circle fit: one shared centre, one radius per camera ---
    # 2*cx*x + 2*cy*y - k_i = x^2+y^2   is LINEAR in (cx, cy, k_i)
    rowsA, rowsb, idx = [], [], []
    for i, n in enumerate(names):
        p = data[n]
        for x, y in zip(p[:, 1], p[:, 2]):
            a = np.zeros(2 + len(names))
            a[0], a[1] = 2 * x, 2 * y
            a[2 + i] = -1.0
            rowsA.append(a); rowsb.append(x * x + y * y); idx.append(i)
    A = np.array(rowsA); b = np.array(rowsb); idx = np.array(idx)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0], sol[1]
    k = sol[2:]
    r = np.sqrt(np.maximum(cx * cx + cy * cy - k, 0.0))

    print("\nROTATION CENTRE  x=%+.4f  y=%+.4f m   (layout frame)" % (cx, cy))
    print("  X and Y need no anchor - the radius is a DIFFERENCE from this centre,")
    print("  so wherever the layout puts its origin cancels out.")
    zlab = "height mm" if origin_height is not None else "z vs origin"
    print("\n%-16s %10s %12s %12s %12s"
          % ("camera", "radius mm", "fit resid mm", zlab, "arc deg"))
    res_all = {}
    for i, n in enumerate(names):
        p = data[n]
        rad = np.hypot(p[:, 1] - cx, p[:, 2] - cy)
        res = rad - r[i]
        res_all[n] = res
        z = p[:, 3].mean() + (origin_height or 0.0)
        print("%-16s %10.1f %12.1f %12.1f %11.1f"
              % (n, 1000 * r[i], 1000 * res.std(), 1000 * z, arcs[n]))
    if origin_height is None:
        print("\n  !! Z IS NOT ANCHORED. Those heights are relative to the layout's Z=0,")
        print("     which for a surveyed bench layout is the anchor TAG, not the floor.")
        print("     Measure that tag's height above the floor once and pass")
        print("     --origin-height <metres>.  X/Y do not need this; Z does, because")
        print("     robotToCamera wants height above the robot, not above a tag.")
        print("     (The official FRC field layout already carries true heights, so on")
        print("      a real field this is a non-issue - pass --origin-height 0.)")

    # --- relative geometry between cameras, at matched times ---
    if len(names) >= 2:
        print("\nRELATIVE MOUNTING  (independent of where 'robot forward' is)")
        base = names[0]
        tb = data[base][:, 0]
        for n in names[1:]:
            pa, pb = [], []
            for row in data[n]:
                j = int(np.argmin(np.abs(tb - row[0])))
                if abs(tb[j] - row[0]) < 0.030:
                    pa.append(data[base][j]); pb.append(row)
            if len(pa) < 20:
                print("  %-14s too few simultaneous solves (%d)" % (n, len(pa)))
                continue
            pa = np.array(pa); pb = np.array(pb)
            ba = np.arctan2(pa[:, 2] - cy, pa[:, 1] - cx)
            bb = np.arctan2(pb[:, 2] - cy, pb[:, 1] - cx)
            dbear = np.degrees(np.angle(np.exp(1j * (bb - ba))))
            dyaw = np.degrees(np.angle(np.exp(1j * (pb[:, 4] - pa[:, 4]))))
            sep = 1000 * np.linalg.norm(pb[:, 1:4] - pa[:, 1:4], axis=1)
            print("  %s -> %s   (%d paired)" % (base, n, len(pa)))
            print("     bearing apart %+7.2f deg  sd %.2f    <- angle about the pivot"
                  % (dbear.mean(), dbear.std()))
            print("     yaw apart     %+7.2f deg  sd %.2f    <- mounting angle difference"
                  % (dyaw.mean(), dyaw.std()))
            print("     separation    %7.1f mm   sd %.1f" % (sep.mean(), sep.std()))

    # A circle fit is only meaningful if the residual is small COMPARED TO THE
    # RADIUS. Pivoting about a point close to the cameras gives a tiny arm that
    # pose noise swamps - measured: a 28 mm radius with a 13 mm residual, which
    # is not a measurement at all. Refuse rather than print a plausible number.
    bad = [n for i, n in enumerate(names) if res_all[n].std() > 0.15 * r[i]]
    worst = max(res_all.values(), key=lambda v: v.std())
    print("\nFIT QUALITY: worst per-camera radial residual %.1f mm sd" % (1000 * worst.std()))
    for i, n in enumerate(names):
        print("   %-14s residual is %3.0f%% of the radius" % (n, 100 * res_all[n].std() / max(r[i], 1e-9)))
    if bad:
        print("\n*** THESE RADII ARE NOT USABLE: %s" % ", ".join(bad))
        print("    The residual is more than 15% of the radius, which means the")
        print("    pivot was too close to the cameras - the arm is smaller than the")
        print("    pose noise. Move the pivot 200-400 mm AWAY from the lenses and")
        print("    rotate again. (The yaw between cameras above is unaffected; it")
        print("    does not come from the circle fit.)")
    print("A residual much larger than your pose jitter means either the rig did not")
    print("rotate about a FIXED axis, or one camera's calibration disagrees with the others.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["record", "solve"])
    ap.add_argument("file")
    ap.add_argument("seconds", nargs="?", type=float, default=60.0)
    ap.add_argument("--host", default="photonvision.local")
    ap.add_argument("--cameras", default="OV9281,OV9281 (1)",
                    help="comma-separated nicknames")
    ap.add_argument("--origin-height", type=float, default=None,
                    help="metres from the floor (or robot origin plane) up to the "
                         "layout's Z=0. A surveyed bench layout anchors Z to a TAG, so "
                         "measure that tag's height once. The official FRC field layout "
                         "already uses true heights - pass 0 there.")
    a = ap.parse_args()
    names = [c.strip() for c in a.cameras.split(",")]
    if a.mode == "record":
        record(a.file, a.seconds, a.host, names)
    else:
        solve(a.file, a.origin_height)
