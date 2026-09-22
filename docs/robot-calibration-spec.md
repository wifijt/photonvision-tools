# Robot calibration: odometry, Pigeon 2, and robotToCamera

**Status: spec, not built.** Written 2026-09-22, before the protobot exists.

Target: swerve, Pigeon 2, integrated drive encoders + CANcoders for steer,
robot code in Java/WPILib. Analyzer in Python on a laptop; the robot side is a
thin logger implementing a documented NetworkTables contract.

---

## The idea

Do not tape-measure the robot and hope vision agrees. **The tags are the
instrument.** A surveyed AprilTag layout is an absolute, drift-free position
and heading reference bolted to the field, and every constant below can be
measured against it.

That inverts the usual order. You do not calibrate odometry and then check
vision; you calibrate the gyro against vision, odometry against the gyro, and
`robotToCamera` last, because it is defined relative to a robot origin that
only exists once the first two do.

## What it solves for

| | parameter | from |
|---|---|---|
| **Pigeon** | yaw scale factor | vision yaw over a spin |
| | drift rate, deg/s | vision yaw with the robot parked |
| | **mount yaw** relative to robot forward | motion — see below |
| **Odometry** | effective wheel radius (shared, then per-module) | straight drive vs vision translation |
| | module translations, as a check on CAD | spin vs straight disagreement |
| | common steer zero bias | odometry travel direction vs vision travel direction |
| **Vision** | `robotToCamera` per camera, 6 DOF | bidirectional runs + `calibrate_mount.py` |
| **Both** | vision timestamp offset | the same path driven in both directions |

**Pigeon mount yaw is the one you cannot get from a wizard.** Gravity fixes
pitch and roll — an accelerometer at rest sees down. It cannot see yaw. If the
Pigeon is bolted down rotated a few degrees from robot forward, nothing
stationary will ever tell you, and the error goes straight into every pose
estimate as a heading bias. Motion against an absolute heading reference is the
only way, and vision is that reference.

## What already exists and is reused

- `mount/calibrate_mount.py` — solves camera radius, bearing and
  camera-to-camera yaw from a spin, validated to ~1° on a bench. It states its
  own boundary: *"robot forward — YES, needs an anchor. Needs one straight
  drive."* This tool supplies that anchor.
- `survey/solve_layout.py` — the tag layout. **Hard prerequisite.**
- `viz/field_viewer.py` / `analyze_rig_log.py` — the log-and-fit shape, jsonl
  in, diagnosis out. Same pattern here.

## Prerequisite that will silently ruin everything

**A wrong tag layout is absorbed into the answers.** Calibrate against a layout
that is 20 mm out and the fit will quietly put that error into wheel radius and
report high confidence. The tool refuses to run without a layout that carries a
stated uncertainty, and it propagates that uncertainty into every output rather
than hiding it. Cameras also need a calibration at their active resolution —
without one, `solvePNPEnabled` publishes nothing at all.

---

## Degeneracies, and the drive patterns that break them

This is the whole design. Each routine exists to separate two things that look
identical in the wrong data.

### Latency vs. camera translation

At constant velocity a vision timestamp offset `dt` puts the camera `v·dt`
behind where it should be — **indistinguishable** from mounting the camera
`v·dt` further back. Laps at a steady speed cannot separate them, no matter how
many.

> **Drive the same path in both directions.** Reversing `v` flips the sign of
> `v·dt` while the mount translation stays put. One sign-flip separates them
> completely.

### Latency vs. wheel scale

Wheel scale error grows with **distance**; latency error grows with **speed**.

> **The same straight run at two speeds.** Same distance, different `v`.

### Gyro scale vs. module geometry

Spinning in place, a 2% small wheel radius and a 2% small module radius produce
identical encoder counts for the same rotation. Vision yaw breaks it — it is
absolute — but only for the gyro. Separating wheel radius from module radius
needs CAD module positions taken as given, or the cross-check below.

> **Spin, then drive straight, and require the wheel radius to agree.** If the
> spin says one radius and the straight drive says another, your module
> translations are wrong. That disagreement is the most useful number the tool
> produces, because it is the one thing that cannot be explained away.

### Steer zero offsets

Swerve odometry integrates *measured* steer angles, so a zero offset makes
odometry believe the robot travelled in a direction rotated by that offset.
A **common** bias across modules is directly observable as the angle between
odometry's travel direction and vision's. Per-module offsets do not show up as
a clean angle — they show up as scrub, excess residual, and wheel slip, and the
tool reports them as a residual per module rather than pretending to solve them.

---

## Routines

Four. The first three are fitted; the fourth is never fitted and exists to
falsify the first three.

1. **Static** — 3 min parked, tags in view. Pigeon drift rate, vision noise
   floor, timestamp sanity, and the standard deviations the pose estimator
   should actually be configured with. No motion, no risk, and it is the only
   routine that can run the day the protobot first powers on.
2. **Spin** — two turns each direction, slowly, tags in view throughout.
   Gyro scale, module effective radii, camera radius and bearing.
3. **Straight, bidirectional, two speeds** — four runs down a known-clear lane.
   Wheel radius, latency offset, camera translation, common steer bias.
4. **Free drive** — a figure-8 or just driving around. **Held out of the fit
   entirely.** The report's headline number is the residual on this run: how
   far vision and odometry disagree on data the fit never saw. Fitting error is
   not evidence; held-out error is.

## Outputs

A report with a number, an uncertainty and a provenance for each parameter, and
a paste-ready Java constants block. Every value carries which routine produced
it, so a suspicious number leads back to the run that made it.

Values that are *worse* than the defaults are reported as such rather than
applied. A calibration that makes things worse is a real outcome and the tool
says so.

## Safety

This one drives the robot, which is the exact inversion of photontune — that
tool refuses to run while the robot is enabled; this one only runs while it is.
Same hazard, opposite polarity.

- Routines are WPILib commands. Disable stops them, because disable stops
  everything; there is no path that keeps motors commanded across a disable.
- Every routine declares its required clear space **before** it moves, and the
  operator confirms.
- Speed and acceleration limits are arguments with conservative defaults, not
  constants buried in the source.
- The robot-side logger never writes setpoints. Commanding and logging are
  separate classes so that a logging bug cannot move the robot.

## Build order

Everything except the Java half can be built and validated **before the
protobot exists**, which is the point of putting the simulator first.

1. **Simulator** — a synthetic swerve with known-truth parameters, configurable
   noise, and deliberately injected latency, gyro scale error and steer bias.
2. **Analyzer** — fits against the simulator. The acceptance test is recovering
   injected truth, the same standard `calibrate_mount.py` was held to
   (*"synthetic ground truth recovered exactly"*).
3. **Adversarial pass** — sabotage each parameter in the simulator and require
   the analyzer to catch it, in the style of `sabotage_test.py`. A fit that
   cannot fail is not a measurement.
4. **Java logger + routines** — once there is a robot to run them on.
5. **Field validation** — the held-out residual against a real run.

Steps 1-3 are the majority of the work and need no hardware.

## Open

- **Repo.** This spec sits in `photonvision-tools/docs/` because it is a
  document and moving it is free. The tool itself contains Java and is not
  really a PhotonVision tool, so it probably wants its own repo.
- **Per-module wheel radii.** Worth solving only if the shared-radius residual
  demands it. Start shared.
- **Pitch and roll of `robotToCamera`** are observable from tag geometry but
  noisy, and on flat carpet they barely matter. Report them, do not chase them.
