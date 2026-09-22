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

Four artifacts: a console report, a paste-ready Java block, a JSON record, and a
before/after field trace. Every parameter carries a value, an uncertainty and a
**provenance** — which routine produced it — so a number that looks wrong leads
straight back to the run that made it.

### The report

```
── calibration report ──  2026-10-14 19:22   4 routines, 11.3 min of data
   layout : shop-wall-2026-10-02.json   14 tags, stated uncertainty +/- 6 mm
   surface: SHOP FLOOR, sealed concrete - NOT competition carpet

PIGEON 2
   yaw scale factor      0.99934   +/- 0.00021    spin, 4 turns both ways
   drift rate            0.0021 deg/s             static, 180 s
   mount yaw            -1.84 deg  +/- 0.09       spin + straight
        Not obtainable at rest - gravity fixes pitch and roll, never yaw.
        1.84 deg is 32 mm of cross-track error for every metre travelled.

ODOMETRY
   wheel radius          0.049810 m +/- 0.000095  straight x4, 31.8 m total
        nominal 0.050800 - effective is 1.95% smaller. Tread compression.
   cross-check, spin     0.049775 m +/- 0.000210  spin
        agrees within 0.15 sigma: module translations are consistent with CAD
   common steer bias    +0.37 deg  +/- 0.06       odometry vs vision heading
   per-module residual   FL 3.1 mm   FR 2.8   BL 3.4   BR 11.7 mm
        BR is 3.5x its siblings, which is not noise. Check its CANcoder offset
        and its tread. A shared-radius fit cannot absorb one odd module, so
        this is reported rather than quietly averaged away.

VISION
   capture time offset   23.4 ms +/- 1.8          bidirectional straight runs
        separated from mount translation by the direction flip: forward and
        reverse disagreed by 46.9 mm at 1.0 m/s, which is 2 x v x dt.
   robotToCamera  OV9281
        x  +0.2413 m +/- 0.0021     y  -0.1524 m +/- 0.0019
        z  +0.2032 m +/- 0.0094     <- weak, comes only from tag heights
        roll +0.21 +/- 0.81   pitch -14.92 +/- 0.74   yaw -44.86 +/- 0.13 deg
        roll and pitch are noisy by nature on flat ground. Reported, not chased.

HELD OUT - free drive, 2.1 min, used in NO fit
   position residual     before  84.2 mm rms       after  21.3 mm rms
   heading residual      before   2.11 deg         after   0.35 deg
   worst single sample   before 214 mm             after    58 mm

ACCURACY, NOT PRECISION
   The +/- above are the fit own scatter. The layout +/- 6 mm sits underneath
   all of it: over 31.8 m of travel that is 0.019% on wheel radius, whatever
   the fit claims. To do better, survey better.

VERDICT: apply. Held-out position error fell 75%.
```

Three things that report does on purpose.

**The headline is a number the fit never saw.** Residual on held-out data, before
and after. A fit always explains its own data; that is not evidence of anything.

**It separates precision from accuracy.** The `+/-` are the fit's internal
scatter, and `calibrate_mount.py` already learned this lesson the hard way — a
bootstrap will happily claim sub-millimetre precision on a measurement that is
10 mm from the truth. The layout uncertainty is a floor under every number
derived from it, stated as such.

**It surfaces hardware, not just constants.** The per-module residual above is
the tool finding a bad CANcoder offset or a worn tread. That is worth more than
any constant it prints, and a fit that silently averaged it into a shared radius
would have hidden it.

### The constants

```java
// Generated by robotcal 2026-10-14 19:22 from run 2026-10-14T190422Z
// Surface: shop floor (sealed concrete). Re-run on carpet before competition.
// Held-out validation: 21.3 mm rms position, 0.35 deg heading.
public static final double kWheelRadiusMeters   = 0.049810;  // +/- 0.000095
public static final double kPigeonYawScale      = 0.99934;   // +/- 0.00021
public static final double kPigeonMountYawDeg   = -1.84;     // +/- 0.09
public static final double kVisionCaptureOffset =  0.0234;   // s, +/- 0.0018
public static final Transform3d kRobotToCam0 = new Transform3d(
    new Translation3d(0.2413, -0.1524, 0.2032),
    new Rotation3d(Math.toRadians(0.21), Math.toRadians(-14.92),
                   Math.toRadians(-44.86)));
```

Copied, not applied. Nothing reaches the robot without a human pasting it.

### The JSON

Every sample, every residual, every parameter with its covariance, the routines
and their timestamps, the layout file and its hash. This is what makes a
calibration comparable to the one before it — drift in wheel radius across a
season is a tread-wear measurement, and it only exists if the records do.

### The field trace

Odometry path and vision path overlaid, before and after, in the style of
`viz/field_viewer.py`. Not decoration: a systematic error has a **shape**.
A wheel-radius error makes the paths diverge with distance, a mount-yaw error
bows them apart on turns, and a latency error separates them only while moving.
Reading the shape is often faster than reading the numbers.

### When it will not apply

Three outcomes are not "here are your constants":

- **Worse than what you had.** Held-out residual did not improve. Reported as a
  failure with the numbers, and the constants block is withheld — not printed
  with a warning above it, because a printed constant gets pasted.
- **Not observable from this data.** Too short a straight run, too few tags, a
  spin that never completed. The parameter is reported as *not measured* rather
  than as a weakly-constrained guess.
- **The layout is the limit.** When layout uncertainty dominates the fit, it
  says so and tells you that re-running the calibration cannot help. Surveying
  again is the only thing that will.

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

## Operating it

### Who drives

**The robot drives the profile; you hold a button the whole time.** Each routine
is a WPILib command bound to hold-to-run on the operator controller, so letting
go stops it — a dead-man switch that does not depend on the laptop, the network,
or the tool being correct. The laptop *arms* a routine; a human *commits* to it.
Nothing the analyzer does can put the robot in motion.

This is why the routines are driven rather than hand-driven. Hand-driving cannot
hold a speed steady enough to separate latency from wheel scale, and the whole
design rests on that separation.

### Logging: one file, one clock

The robot subscribes to PhotonVision over NetworkTables and logs vision
**alongside** odometry and gyro through `DataLogManager`, to a USB stick on the
roboRIO.

That is the deliberate part. Logging both streams on the roboRIO puts them in
the **same timebase**, so the only unknown left is PhotonVision's capture-time
offset — which is a parameter the tool solves. Logging vision on the laptop
instead would add network clock skew on top of it, and two unknowns that sum
cannot be separated.

Full rate, and it survives a wifi dropout, which NT streaming to a laptop does
not. The laptop still watches NT live, but only to show the operator what is
happening. The analyzer reads the `.wpilog` afterwards —
`wpiutil.log.DataLogReader`, confirmed available in Python, so there is no
conversion step.

### A session, start to finish

Roughly 30 minutes on the floor the first time.

| | needs | time | moves? |
|---|---|---|---|
| 0. Survey the tags | `solve_layout.py` | once, not per session | no |
| 1. Static | tags in view, robot parked | 3 min | no |
| 2. Spin | ~2 m circle | 2 min | in place |
| 3. Straight x4 | **a clear 6-8 m lane with tags visible** | 10 min | yes |
| 4. Free drive | open space | 2 min | yes |
| 5. Analyze | laptop | 1 min | no |
| 6. Re-run free drive | to confirm it improved | 2 min | yes |

Step 6 is the point. The tool has to demonstrate that the residual dropped, on a
run that was not fitted, or the calibration has not earned the constants it is
asking you to paste.

### Where you can actually do this

- **Static and spin fit in a garage.** They need tags in view and about 2 m.
- **The straight runs do not.** 6-8 m of clear lane with tags visible throughout
  is a gym or a long hallway. Shorter runs work but the wheel-radius uncertainty
  scales inversely with distance, so a 2 m run buys roughly a quarter of the
  precision of an 8 m one.
- **Step 1 alone is useful on day one.** Pigeon drift, vision noise floor and
  the pose-estimator standard deviations need no drivetrain tuning and no space
  at all — it is the first thing worth running on a protobot that has never
  moved.

### The carpet caveat

Effective wheel radius is a property of the **surface**, not the robot. A number
measured on a shop floor is wrong on competition carpet, and it moves with tread
wear. Same rule as photontune: calibrate where you will play. The report states
the surface it was measured on, because a constant without that context is a
trap.

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
