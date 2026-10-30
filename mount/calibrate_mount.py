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
  pitch, roll                           no anchor - gravity defines them, so
                                        they are absolute. Constant through the
                                        spin, so their sd is the quality check.
  Z (camera height)                     YES - pass --origin-height. A surveyed
                                        bench layout anchors Z to a TAG, not the
                                        floor. The official FRC field layout
                                        already carries true heights (pass 0).
  "robot forward"                       YES - needs one straight drive. Not
                                        available, and not meaningful, on a bench.
  gyro scale and sign                   no anchor - the spin sweeps a known
                                        amount of yaw, so the RATIO is a direct
                                        measurement. Pass --gyro-topic.
  gyro-to-forward offset                YES - the straight drive again. Mixes
                                        the steer crab with the gyro's zero, so
                                        read _gyro_offset before applying it.

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
import json
import math
import sys
import time

import numpy as np

PER_BIN = 12          # max samples per 2-degree slice of the arc

# Radial residual that says the SPIN was bad, in mm. Absolute, not a fraction
# of the radius: x/y are a difference from the fitted centre, so their error is
# bounded by this residual however short the lever arm is. Measured on a
# two-camera bench rig: 1.5-3 mm with a fixed axis, 13 mm with a hand pivot
# that wandered. 5 mm sits between those.
RESID_LIMIT_MM = 5.0

# A gyro published as a bare number carries no units. These are the only two it
# is ever in, and the spin tells us which: see _gyro_track.
GYRO_UNITS = (("degrees", 180.0 / math.pi), ("radians", 1.0))

GYRO_HINTS = ("gyro", "yaw", "heading", "angle", "rotation", "pigeon", "imu",
              "navx", "theta")


def _gyro_candidates(inst):
    """List NT number topics that look like a heading, so you can pick one.

    There is no standard place for the gyro on NetworkTables - it is wherever
    the team's robot code happened to publish it. A default would be wrong on
    every robot but one, so print what is actually on the wire instead.
    """
    found = []
    for t in inst.getTopics():
        name = t.getName()
        if t.getTypeString() not in ("double", "float", "int", "int64"):
            continue
        if not any(h in name.lower() for h in GYRO_HINTS):
            continue
        try:
            v = inst.getDoubleTopic(name).subscribe(float("nan")).get()
        except Exception:
            v = float("nan")
        found.append((name, t.getTypeString(), v))
    print("\nNT number topics that look like a heading:")
    if not found:
        print("  (none). Either robot code is not publishing one, or it is not")
        print("  named anything like gyro/yaw/heading. Check the PhotonVision or")
        print("  dashboard NT tree, then pass --gyro-topic /Full/Topic/Name.")
        return
    for name, ty, v in sorted(found):
        print("  %-52s %-7s %s" % (name, ty, "nan" if v != v else "%.3f" % v))
    print("  Pick the one that is the ROBOT's heading - not a module angle, not")
    print("  a setpoint - and pass it as --gyro-topic. Rotate the robot by hand")
    print("  and watch which one moves if you are not sure.")


def _gyro_track(camyaw_unwrapped, graw):
    """Units, sign and scale of the gyro, measured against the spin.

    The spin is the only place this is observable, and it needs no anchor: the
    camera yaw sweeps a known amount, so the RATIO of the two is a direct
    measurement. A healthy gyro gives scale +1.000 in one of two unit systems.

    Returns (rel_resid_rad, unit, scale, how, to_rad) or None.
    """
    a = np.asarray(camyaw_unwrapped, dtype=float)
    a = a - a.mean()
    if float(a @ a) < 1e-9:
        return None
    best = None
    # Robot code publishes yaw either continuous (Pigeon2 accumulates past 360)
    # or wrapped to +/-180. Try both readings and let the fit decide.
    for how, g in (("as published", np.asarray(graw, dtype=float)),
                   ("unwrapped, degrees", np.degrees(np.unwrap(np.radians(graw)))),
                   ("unwrapped, radians", np.unwrap(np.asarray(graw, dtype=float)))):
        if not np.all(np.isfinite(g)):
            continue
        b = g - g.mean()
        slope = float(a @ b / (a @ a))
        if abs(slope) < 1e-9:
            continue
        # Residual expressed in TRUE yaw radians, so it is comparable across
        # unit hypotheses. A plain residual is not - it just follows the units.
        rel = float(np.std(b - slope * a) / abs(slope))
        for unit, per_rad in GYRO_UNITS:
            scale = slope / per_rad
            if 0.5 < abs(scale) < 2.0:
                cand = (rel, unit, scale, how, 1.0 / per_rad)
                if best is None or cand[0] < best[0]:
                    best = cand
    return best


def q2R(w, x, y, z):
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def record(path, seconds, host, names, gyro_topic=None):
    import ntcore
    from photonlibpy.photonCamera import PhotonCamera
    inst = ntcore.NetworkTableInstance.getDefault()
    inst.startClient4("mount-cal")
    inst.setServer(host, ntcore.NetworkTableInstance.kDefaultPort4)
    cams = {n: PhotonCamera(n) for n in names}
    time.sleep(2.5)
    gyro = None
    if gyro_topic:
        gyro = inst.getDoubleTopic(gyro_topic).subscribe(float("nan"))
        if gyro.get() != gyro.get():
            print("!! %s is not publishing yet - recording anyway, but the gyro"
                  % gyro_topic)
            print("   column will be nan and the gyro checks will be skipped.")
            _gyro_candidates(inst)
    else:
        _gyro_candidates(inst)
        print("\n  (recording WITHOUT a gyro column - pass --gyro-topic to get")
        print("   the Pigeon scale check and the gyro-to-forward offset)")
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
            # ZYX (WPILib Rotation3d) order: roll about X, pitch about Y, yaw
            # about Z. Pitch via atan2 rather than asin(-R[2,0]) so it stays
            # well-conditioned near +/-90 deg. Columns APPENDED, never inserted:
            # solve() indexes 1..4 positionally and a .npz recorded before this
            # change has only 5 columns.
            rows[n].append([md.captureTimestampMicros / 1e6, b.X(), b.Y(), b.Z(),
                            math.atan2(R[1, 0], R[0, 0]),
                            math.atan2(-R[2, 0], math.hypot(R[0, 0], R[1, 0])),
                            math.atan2(R[2, 1], R[2, 2]),
                            gyro.get() if gyro is not None else float("nan")])
        if time.time() - t0 - last > 10:
            last = time.time() - t0
            print("  %3ds  %s" % (last, "  ".join("%s=%d" % (n, len(rows[n])) for n in names)),
                  flush=True)
        time.sleep(0.002)
    np.savez(path, **{n: np.array(v) for n, v in rows.items()}, names=np.array(names))
    print("\nwrote %s" % path)
    for n in names:
        print("  %-14s %d solves" % (n, len(rows[n])))


def _forward(names, spin, r, cx, cy, zs, tilt, K, path, have_tilt, gyro=None):
    """Resolve 'robot forward' from one straight drive, then emit robotToCamera.

    WHAT "FORWARD" MEANS HERE, stated plainly because it is a choice and not a
    measurement: forward is THE DIRECTION THE ROBOT ACTUALLY TRAVELLED when
    commanded straight. It is not the Pigeon's zero and it is not the chassis
    centreline. If the steer offsets are a degree off, the robot crabs, and
    that crab is baked into every robotToCamera this emits.

    That is the right choice for vision: robotToCamera then lives in the same
    frame the drivetrain actually moves in, so vision and odometry agree. It is
    the wrong choice if you wanted to MEASURE the crab - for that you need an
    independent heading, and the earlier audits showed a camera cannot supply
    one (a closed loop hides the error in the sensor it closes on).
    """
    d = np.load(path, allow_pickle=True)
    dnames = [str(x) for x in d["names"]]
    drive = {n: d[n] for n in dnames if n in names and d[n].size}
    missing = [n for n in names if n not in drive]
    if missing:
        print("\n!! --forward has no data for %s - cannot place %s"
              % (", ".join(missing), ", ".join(missing)))
    usable = [n for n in names if n in drive]
    if not usable:
        print("\n!! --forward recording has no cameras in common with the spin")
        return

    print("\n" + "=" * 68)
    print("ROBOT FORWARD, from the straight drive")
    print("=" * 68)

    # --- each camera's path gives an INDEPENDENT estimate of the direction ---
    # Every point on a rigid body translating without rotation moves along the
    # same vector, so the cameras must agree. Disagreement means the robot
    # turned, or a camera's pose stream is bad.
    dirs, spans, straight = {}, {}, {}
    for n in usable:
        q = drive[n]
        xy = q[:, 1:3]
        c = xy - xy.mean(axis=0)
        # Total least squares: the principal axis, not a y-on-x regression,
        # because a drive running near the layout's y axis has an infinite slope.
        _u, _s, vt = np.linalg.svd(c, full_matrices=False)
        v = vt[0]
        # 180 deg sign ambiguity: resolve by where it went, not where the line lies.
        if np.dot(xy[-1] - xy[0], v) < 0:
            v = -v
        dirs[n] = math.atan2(v[1], v[0])
        along = c @ v
        spans[n] = float(along.max() - along.min())
        straight[n] = float(np.abs(c @ np.array([-v[1], v[0]])).std())

    print("\n%-16s %10s %12s %12s %12s"
          % ("camera", "n", "travel mm", "straight mm", "heading deg"))
    for n in usable:
        print("%-16s %10d %12.0f %12.1f %12.2f"
              % (n, len(drive[n]), 1000 * spans[n], 1000 * straight[n],
                 math.degrees(dirs[n])))

    # --- did the robot ROTATE during the "straight" drive? -------------------
    # A drive with rotation in it is not a heading reference. Yaw must be flat.
    rot = {n: math.degrees(np.ptp(np.unwrap(drive[n][:, 4]))) for n in usable}
    worst_rot = max(rot.values())
    if worst_rot > 3.0:
        print("\n*** THE ROBOT ROTATED %.1f deg DURING THE DRIVE." % worst_rot)
        print("    That is not a straight drive and the heading it implies is")
        print("    not robot forward. Drive straight, no steering input, and")
        print("    re-record. Refusing to emit a transform.")
        return

    # --- is the drive long enough to define a direction at all? -------------
    # Angular uncertainty of the fitted direction is about (perpendicular
    # scatter) / (length travelled). A short drive cannot give a heading.
    shortest = min(spans.values())
    implied = math.degrees(max(straight[n] / max(spans[n], 1e-9) for n in usable))
    print("\n  shortest travel %.0f mm, implied heading uncertainty ~%.2f deg"
          % (1000 * shortest, implied))
    if shortest < 0.5:
        print("\n*** TRAVEL TOO SHORT (%.0f mm). Under ~500 mm the direction fit"
              % (1000 * shortest))
        print("    is swamped by pose noise and every yaw below inherits it.")
        print("    Drive 2-3 m in a straight line and re-record.")
        return

    # --- the cameras must agree on the direction ----------------------------
    if len(usable) >= 2:
        ref = dirs[usable[0]]
        disagree = max(abs(math.degrees(math.atan2(math.sin(dirs[n] - ref),
                                                   math.cos(dirs[n] - ref))))
                       for n in usable)
        print("  cameras disagree on heading by at most %.2f deg" % disagree)
        if disagree > 2.0:
            print("\n  !! that is larger than the fit uncertainty. One camera's")
            print("     pose stream disagrees with the others - suspect its")
            print("     calibration before trusting anything below.")

    # The rigid body shares one direction; average what the cameras saw.
    theta = float(np.angle(np.mean(np.exp(1j * np.array([dirs[n] for n in usable])))))
    print("\n  ROBOT FORWARD = %+.2f deg in the layout frame" % math.degrees(theta))

    # --- split K into bearing and yaw ---------------------------------------
    #   camyaw_i(drive) = theta + Y_i      ->  Y_i = camyaw_i - theta
    #   K_i = B_i - Y_i                    ->  B_i = K_i + Y_i
    Y, B = {}, {}
    for n in usable:
        camyaw = float(np.angle(np.mean(np.exp(1j * drive[n][:, 4]))))
        Y[n] = math.atan2(math.sin(camyaw - theta), math.cos(camyaw - theta))
        B[n] = math.atan2(math.sin(K[n] + Y[n]), math.cos(K[n] + Y[n]))

    # --- independent cross-check -------------------------------------------
    # The SPIN measured yaw-apart between cameras without ever seeing the drive.
    # The drive now gives each yaw separately. They must agree, and this is the
    # only check here that uses two unrelated measurements.
    if len(usable) >= 2:
        print("\n  CROSS-CHECK  spin's yaw-apart vs the drive's separate yaws")
        base = usable[0]
        tb = spin[base][:, 0]
        for n in usable[1:]:
            pa, pb = [], []
            for row in spin[n]:
                j = int(np.argmin(np.abs(tb - row[0])))
                if abs(tb[j] - row[0]) < 0.030:
                    pa.append(spin[base][j]); pb.append(row)
            if len(pa) < 20:
                continue
            pa, pb = np.array(pa), np.array(pb)
            spin_dyaw = math.degrees(np.angle(np.mean(np.exp(1j * (pb[:, 4] - pa[:, 4])))))
            drive_dyaw = math.degrees(math.atan2(math.sin(Y[n] - Y[base]),
                                                 math.cos(Y[n] - Y[base])))
            gap = abs(math.degrees(math.atan2(
                math.sin(math.radians(spin_dyaw - drive_dyaw)),
                math.cos(math.radians(spin_dyaw - drive_dyaw)))))
            flag = "" if gap < 1.0 else "   <<< DISAGREES"
            print("    %s -> %-14s spin %+7.2f   drive %+7.2f   gap %.2f deg%s"
                  % (base, n, spin_dyaw, drive_dyaw, gap, flag))
        print("    A gap much over ~1 deg means the drive was not straight or the")
        print("    spin axis was not fixed. The two measurements are independent,")
        print("    so agreement here is real evidence; it is not self-fulfilling.")

    # --- emit ---------------------------------------------------------------
    print("\n" + "=" * 68)
    print("robotToCamera")
    print("=" * 68)
    print("%-16s %9s %9s %9s %9s %9s %9s"
          % ("camera", "x m", "y m", "z m", "roll", "pitch", "yaw"))
    out = {}
    for i, n in enumerate(names):
        if n not in usable:
            continue
        x = r[names.index(n)] * math.cos(B[n])
        y = r[names.index(n)] * math.sin(B[n])
        z = zs[n]
        roll = tilt[n][2] if have_tilt else 0.0
        pitch = tilt[n][0] if have_tilt else 0.0
        yaw = math.degrees(Y[n])
        out[n] = dict(x=x, y=y, z=z, roll_deg=roll, pitch_deg=pitch, yaw_deg=yaw)
        print("%-16s %+9.4f %+9.4f %+9.4f %+9.2f %+9.2f %+9.2f"
              % (n, x, y, z, roll, pitch, yaw))
    if not have_tilt:
        print("\n  !! roll and pitch are 0 because this spin predates those")
        print("     columns. Re-record the spin to get them - a camera aimed at")
        print("     tags is normally pitched up, and 0 is wrong, not neutral.")

    print("\nJava:")
    for n, v in out.items():
        print("  // %s" % n)
        print("  new Transform3d(")
        print("      new Translation3d(%.4f, %.4f, %.4f),"
              % (v["x"], v["y"], v["z"]))
        print("      new Rotation3d(Math.toRadians(%.2f), Math.toRadians(%.2f), "
              "Math.toRadians(%.2f)));" % (v["roll_deg"], v["pitch_deg"], v["yaw_deg"]))
    print("\nJSON:")
    print("  " + json.dumps(out, indent=2).replace("\n", "\n  "))
    print("\n  WPILib robot frame: +x forward, +y LEFT, +z up; Rotation3d takes")
    print("  (roll, pitch, yaw). Forward here is the direction the robot")
    print("  TRAVELLED, so any steer crab is inside these numbers - which is")
    print("  what you want for vision, and not a measurement of the crab.")

    _gyro_offset(usable, drive, theta, gyro)
    return out


def _gyro_offset(usable, drive, theta, gyro):
    """How far the gyro's reported heading sits from the direction of travel.

    WHAT THIS NUMBER IS. The gyro reports G. The robot's real heading in the
    layout frame is H, and it travels along H + c where c is the steer crab. So

        G = H + psi        psi = gyro mount yaw + whatever its zero was set to
        offset = theta - G = c - psi

    which mixes the crab with the gyro's zero. That matters, because it decides
    what the number is good for:

      - You zero the gyro from a VISION pose at init: the offset is absorbed by
        that reset and this number is only a sanity check. Anything over a few
        degrees means the Pigeon is mounted crooked.
      - You zero the gyro MECHANICALLY - square the robot to a wall and press a
        button: then psi is known and nothing absorbs the offset. Apply it, or
        odometry dead-reckons along a heading the robot does not travel.
      - You zeroed it against a known mechanical reference just before this
        recording: then psi is ~0 and this offset IS the crab, which is
        otherwise unobservable here. A camera cannot supply an independent
        heading; a gyro referenced to the chassis can.

    The scale check in solve() is the half that nothing absorbs, so read that
    first.
    """
    if not gyro:
        return
    ref = max(usable, key=lambda n: len(drive[n]))
    if drive[ref].shape[1] < 8:
        print("\n  (the --forward recording has no gyro column, so no gyro offset.")
        print("   Re-record the drive with --gyro-topic to get it.)")
        return
    graw = drive[ref][:, 7]
    if not np.all(np.isfinite(graw)):
        print("\n  (the --forward recording's gyro column is nan - the topic was")
        print("   not publishing during the drive. No offset.)")
        return
    g = np.unwrap(graw * gyro["to_rad"]) / gyro["scale"]

    print("\n" + "=" * 68)
    print("GYRO vs FORWARD")
    print("=" * 68)
    swept = math.degrees(np.ptp(g))
    print("  gyro moved %.2f deg during the drive (from %s)" % (swept, ref))
    if swept > 3.0:
        print("  !! the cameras said the robot did not rotate and the gyro says it")
        print("     did. One of them is wrong. Do not apply anything below until")
        print("     you know which - re-record and see if it repeats.")
    gm = float(np.angle(np.mean(np.exp(1j * g))))
    off = math.degrees(math.atan2(math.sin(theta - gm), math.cos(theta - gm)))
    print("  gyro read %+.2f deg (mean) while the robot travelled %+.2f deg"
          % (math.degrees(gm), math.degrees(theta)))
    print("\n  GYRO -> FORWARD OFFSET  %+.2f deg" % off)
    print("    ADD this to the gyro's reported heading to get the direction the")
    print("    robot actually travels - the same frame robotToCamera above uses.")
    if not gyro.get("ok"):
        print("\n  *** NOT EMITTING A CONSTANT TO PASTE. The scale check above")
        print("      failed, and a gyro with the wrong sign or a stretched scale")
        print("      has no single offset: what it reads depends on the path it")
        print("      took to get there, so the %+.2f deg above is only true for" % off)
        print("      this one recording. Fix the sign and the scale first, then")
        print("      re-record both the spin and the drive.")
        return
    print("\n  Java, where you read the heading:")
    print("    // measured %s, re-measure after any steer-offset change"
          % time.strftime("%Y-%m-%d"))
    print("    private static final Rotation2d kGyroToForward =")
    print("        Rotation2d.fromDegrees(%+.2f);" % off)
    print("    public Rotation2d getHeading() {")
    print("      return pigeon.getRotation2d().plus(kGyroToForward);")
    print("    }")
    print("\n  Put it in CODE, not in the Pigeon's MountPose and not with")
    print("  setYaw(): MountPose is for the physical mount orientation and")
    print("  setYaw fights whatever else zeroes the gyro at init. A constant")
    print("  where the heading is READ composes with both.")
    print("\n  This offset is (crab - gyro zero error), so it is only as stable")
    print("  as the gyro's zero. If you reset the pose from vision at init, that")
    print("  reset already absorbs it and this is a sanity check: over ~3 deg")
    print("  means the Pigeon is mounted crooked.")


def solve(path, origin_height=None, forward_path=None):
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
    # Pitch and roll are present only in recordings made after they were added.
    have_tilt = all(data[n].shape[1] >= 7 for n in names)
    print("\n%-16s %10s %12s %12s %12s"
          % ("camera", "radius mm", "fit resid mm", zlab, "arc deg"))
    res_all = {}
    tilt = {}
    zs = {}
    for i, n in enumerate(names):
        p = data[n]
        rad = np.hypot(p[:, 1] - cx, p[:, 2] - cy)
        res = rad - r[i]
        res_all[n] = res
        z = p[:, 3].mean() + (origin_height or 0.0)
        zs[n] = z
        print("%-16s %10.1f %12.1f %12.1f %11.1f"
              % (n, 1000 * r[i], 1000 * res.std(), 1000 * z, arcs[n]))
        if have_tilt:
            # Circular mean, so a roll sitting near +/-180 does not average to 0.
            # Both are CONSTANT through the spin, which is why the sd doubles as
            # the check: a large sd means the axis was not vertical or the pose
            # stream is noisy, not that the camera is moving.
            pit = np.angle(np.exp(1j * p[:, 5]))
            rol = np.angle(np.exp(1j * p[:, 6]))
            tilt[n] = (np.degrees(np.angle(np.mean(np.exp(1j * pit)))),
                       np.degrees(pit.std()),
                       np.degrees(np.angle(np.mean(np.exp(1j * rol)))),
                       np.degrees(rol.std()))

    if have_tilt:
        print("\nTILT  (needs NO anchor - gravity defines it, and these are")
        print("       absolute, unlike bearing and yaw)")
        print("  %-16s %10s %8s %10s %8s" % ("camera", "pitch deg", "sd", "roll deg", "sd"))
        for n in names:
            pm, ps, rm, rs = tilt[n]
            print("  %-16s %+10.2f %8.2f %+10.2f %8.2f" % (n, pm, ps, rm, rs))
        print("  Camera pitch/roll in the FIELD frame. That equals pitch/roll")
        print("  relative to the ROBOT only while the robot sits level - true on")
        print("  a flat floor, not true on a ramp or with a wheel on a cable.")
        noisy = [n for n in names if tilt[n][1] > 1.0 or tilt[n][3] > 1.0]
        if noisy:
            print("  !! sd above 1 deg on %s - the rotation axis was probably not"
                  % ", ".join(noisy))
            print("     vertical. Pitch and roll should be CONSTANT through a spin.")
    else:
        print("\n  (no pitch/roll in this recording - it predates those columns;")
        print("   re-record to get them)")
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

    # --- the one constant the spin pins without any forward reference -------
    # Through a spin the robot heading theta sweeps, and for each camera
    #     bearing_i(t) = theta(t) + B_i      camyaw_i(t) = theta(t) + Y_i
    # where B_i is the camera's bearing in the ROBOT frame and Y_i its mount
    # yaw. theta cancels in the difference, so
    #     K_i = B_i - Y_i
    # is observable with no anchor at all. What is NOT observable is the split:
    # every camera shares one unknown, "where is robot forward in the layout
    # frame", and that single number is the entire job of the straight drive.
    K = {}
    for i, n in enumerate(names):
        p = data[n]
        bear = np.arctan2(p[:, 2] - cy, p[:, 1] - cx)
        K[n] = float(np.angle(np.mean(np.exp(1j * (bear - p[:, 4])))))
    print("\nANCHOR-FREE CONSTANT  K = bearing - mount yaw, in the robot frame")
    for n in names:
        print("  %-16s K %+8.2f deg" % (n, math.degrees(K[n])))
    print("  One shared unknown remains for the whole rig. Fix 'robot forward'")
    print("  once and every camera's bearing and yaw follow from its K.")

    # --- the gyro, checked against the spin ---------------------------------
    # Scale and sign need no anchor: the camera yaw sweeps a known amount, so
    # the ratio is a direct measurement. This is the half of the gyro that
    # NOTHING downstream absorbs - a constant offset is swallowed by any pose
    # reset, but a scale error compounds over a match.
    gyro = None
    have_gyro = all(data[n].shape[1] >= 8 and np.all(np.isfinite(data[n][:, 7]))
                    for n in names)
    if have_gyro:
        ref = max(names, key=lambda n: len(data[n]))
        p = data[ref]
        fit = _gyro_track(np.unwrap(p[:, 4]), p[:, 7])
        print("\nGYRO CHECK  (from %s, %d samples)" % (ref, len(p)))
        if fit is None:
            print("  the spin did not sweep enough yaw to measure the gyro.")
        else:
            rel, unit, scale, how, to_rad = fit
            # ok gates the pasteable offset further down. A gyro with the
            # wrong sign or a stretched scale has no single constant offset at
            # all - its reading depends on the path it took to get there - so
            # emitting a number to paste would be worse than emitting nothing.
            gyro = dict(unit=unit, scale=scale, to_rad=to_rad,
                        ok=(scale > 0 and abs(scale - 1.0) <= 0.005
                            and math.degrees(rel) <= 1.0))
            print("  published in %s, read %s" % (unit, how))
            print("  SCALE %+.4f   (want +1.0000)" % scale)
            print("  nonlinearity %.2f deg sd about a straight line" % math.degrees(rel))
            if scale < 0:
                print("  !! SIGN IS INVERTED. The gyro counts the opposite way from")
                print("     WPILib, which wants counter-clockwise POSITIVE seen from")
                print("     above. Negate it in robot code before anything else here")
                print("     means much.")
            err = abs(abs(scale) - 1.0)
            if err > 0.005:
                tiltdeg = math.degrees(math.acos(min(1.0, abs(scale)))) if abs(scale) < 1 else 0.0
                print("  !! scale is off by %.1f%%. Over one 360 deg match-long" % (100 * err))
                print("     accumulation that is %.1f deg of drift, and no pose reset"
                      % (360 * err))
                print("     absorbs it. A gyro whose axis is %.0f deg off vertical reads"
                      % tiltdeg)
                print("     cos(tilt) of the truth - fix it in the Pigeon's MountPose,")
                print("     which is exactly what MountPose is for.")
            if math.degrees(rel) > 1.0:
                print("  !! nonlinear by more than 1 deg. Scale is then not the whole")
                print("     story - suspect latency between the two streams, or a")
                print("     spin axis that was not fixed.")
    else:
        print("\n  (no gyro column in this spin - record with --gyro-topic to get")
        print("   the Pigeon scale/sign check and the gyro-to-forward offset)")

    if forward_path:
        _forward(names, data, r, cx, cy, zs, tilt, K, forward_path, have_tilt, gyro)
    else:
        print("\n  No --forward given, so bearing and yaw stay RELATIVE and no")
        print("  robotToCamera can be emitted. Record one straight drive with")
        print("  `record`, then pass it as --forward.")

    # Gate on the ABSOLUTE residual, not on its ratio to the radius.
    #
    # This used to refuse whenever the residual passed 15% of the radius, on the
    # reasoning that a short lever arm is swamped by pose noise. That reasoning
    # is wrong, and synthetic truth says so: with cameras at 15 mm and 8 mm from
    # the axis and 1.5 mm of pose noise, x and y came back within 0.2 mm and
    # 0.1 mm of truth - and the old rule called them unusable at 10% and 18%.
    #
    # x and y are a DIFFERENCE between the camera and the fitted centre, so
    # their error is bounded by the residual in millimetres no matter how short
    # the arm is. The ratio blows up as the radius shrinks because the
    # denominator vanishes, not because the answer got worse. The historical
    # failure that motivated the old rule - a 28 mm radius with a 13 mm
    # residual - was a WANDERING hand pivot, and 13 mm catches that on its own.
    bad = [n for n in names if 1000 * res_all[n].std() > RESID_LIMIT_MM]
    worst = max(res_all.values(), key=lambda v: v.std())
    print("\nFIT QUALITY: worst per-camera radial residual %.1f mm sd" % (1000 * worst.std()))
    print("  That is also the error bar on x and y, in mm. The percentages below")
    print("  are information, not a pass mark - a camera ON the axis has a huge")
    print("  percentage and a correct, near-zero x/y.")
    for i, n in enumerate(names):
        print("   %-14s %5.1f mm sd  =  %3.0f%% of a %.0f mm radius"
              % (n, 1000 * res_all[n].std(),
                 100 * res_all[n].std() / max(r[i], 1e-9), 1000 * r[i]))
    if bad:
        print("\n*** THE SPIN ITSELF IS BAD: %s" % ", ".join(bad))
        print("    Over %.0f mm of radial residual. The rig did not rotate about a"
              % RESID_LIMIT_MM)
        print("    FIXED axis, or one camera's calibration disagrees with the")
        print("    others. x and y inherit this directly, so re-spin about a")
        print("    genuinely fixed point before using them. (Yaw, pitch, roll and")
        print("    z are unaffected - none of them comes from the circle fit.)")
    # A short arm does not spoil x/y and does not blind the residual check
    # either - the residual is absolute, so a wandering axis still shows up
    # (verified: a 10 mm wander gave 5.8 mm sd with the cameras 8-15 mm out).
    # What a short arm really costs is the TAPE MEASURE cross-check, and it
    # makes the bearing ill-determined - harmlessly, since the bearing only
    # ever multiplies a radius that is itself nearly zero.
    near = [n for i, n in enumerate(names)
            if res_all[n].std() > 0.08 * max(r[i], 1e-9)]
    if near and not bad:
        print("\n  NOTE: %s sit(s) close to the rotation axis, hence the large"
              % ", ".join(near))
        print("    percentages above. That is not a problem:")
        print("      - x and y are good to about %.1f mm, the residual. They are a"
              % (1000 * worst.std()))
        print("        difference from the centre and do not care about the arm.")
        print("      - a wandering axis is still caught, because the %.0f mm limit is"
              % RESID_LIMIT_MM)
        print("        absolute and a wander shows up whatever the radius.")
        print("    What you DO lose is the tape-measure cross-check - you cannot")
        print("    usefully tape 10 mm from a pivot to a lens - so the spin/drive")
        print("    yaw cross-check above is your only outside confirmation.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["record", "solve"])
    ap.add_argument("file")
    ap.add_argument("seconds", nargs="?", type=float, default=60.0)
    ap.add_argument("--host", default="photonvision.local")
    ap.add_argument("--cameras", default="OV9281,OV9281 (1)",
                    help="comma-separated nicknames")
    ap.add_argument("--forward", default=None, metavar="FILE",
                    help="a recording of one STRAIGHT drive, made with `record`. "
                         "Resolves the single shared unknown - where robot forward "
                         "points - and turns the relative answers into a full "
                         "robotToCamera per camera.")
    ap.add_argument("--gyro-topic", default=None, metavar="TOPIC",
                    help="full NT topic of the robot's heading as a number, e.g. "
                         "/SmartDashboard/Gyro. Recorded alongside the cameras so "
                         "the spin can check the Pigeon's scale and sign and the "
                         "drive can give the gyro-to-forward offset. Omit it on "
                         "`record` and the candidate topics are listed for you.")
    ap.add_argument("--origin-height", type=float, default=None,
                    help="metres from the floor (or robot origin plane) up to the "
                         "layout's Z=0. A surveyed bench layout anchors Z to a TAG, so "
                         "measure that tag's height once. The official FRC field layout "
                         "already uses true heights - pass 0 there.")
    a = ap.parse_args()
    names = [c.strip() for c in a.cameras.split(",")]
    if a.mode == "record":
        record(a.file, a.seconds, a.host, names, a.gyro_topic)
    else:
        solve(a.file, a.origin_height, a.forward)
