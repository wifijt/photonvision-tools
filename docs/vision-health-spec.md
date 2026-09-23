# Vision health: the standard deviations, and noticing a camera moved

**Status: spec, not built. 2026-09-23.** This is what is left of a much larger
proposal after three adversarial audits killed most of it. The superseded
document is `robot-calibration-spec-SUPERSEDED.md`; its degeneracy analysis is
still correct and still worth reading, and its conclusions are not.

---

## What the audits established

**The field moves more than the robot does.** Field assembly tolerance is
±1/2 inch; teams report reefs 2 inches out of position and 4-12 inches of vision
error between alliance sides at events; WPILib's own maintainer notes tags "can
be mounted in the correct location but with rotational deviations of potentially
several degrees". Teams target 1-2 inches total. A tool measuring 0.3 degrees of
steer drift is an order of magnitude below the noise that dominates the same
auto.

**The community already solved this, correctly, and not by measuring.** The
answer was to stop trusting global pose and align relative to the single tag
being scored on — *"using field element tags to accurately find your 'global'
position… is a fool's errand—they move. Using the tag on what we want to line up
to… works great."* That is not a retreat. Under absolute odometry a calibration
bias is an open-loop error that lands in the final position; under tag-relative
alignment it is a disturbance inside a closed loop.

**The drivetrain half had no evidence behind it.** Gradual steer-offset drift on
modern direct-magnet CANcoders: no published measurement anywhere. Module
mounting shift as a geometric error: not one report, zero mm, zero degrees. The
one drivetrain quantity with confirmed large drift is wheel radius — and
AdvantageKit already ships that routine, carpet nap alone moves it up to 5%, and
the remedy is to re-run the routine rather than to detect that it moved.

**And the residual analyzer could not have done it anyway.** A misaligned
CANcoder is invisible because the steer loop closes on it; a straightedge held
against a *pair* of wheels produces a residual uniform across all four modules,
which names nobody; a robot that merely gained mass flags all four modules
through tyre load transfer; and on 1000 simulated robots the largest residual
named the wrong module 48% of the time. Evidence in `docs/evidence/`.

---

## What survives, and why

One thing the loose architecture does **not** rescue: **a camera that moved.**
A wrong `robotToCamera` corrupts tag-relative alignment exactly as badly as it
corrupts global pose, because the transform sits between the tag observation and
every use of it. This is a confirmed competition failure — a mount that shifted
between events at Championship, mounts deforming and breaking auto-aim — with no
magnitude published anywhere and **no procedure existing anywhere** for obtaining
the transform, let alone noticing it changed. PhotonVision's own documentation
passes `kRobotToCam` with no guidance on where it comes from.

And one thing every team currently guesses: **the vision standard deviations.**
WPILib gives you `visionMeasurementStdDevs` and no way to set it. Measuring it is
the principled version of what the good teams do by hand — 6328 runs a ~4% vision
gain, cut further when the robot is moving. This *serves* the loose architecture
instead of fighting it.

## The tool

Three parts. None needs a robot. All three run against wall tags and the Pi.

### 1. Measured `visionMeasurementStdDevs`, by range and tag count

The output is a table ready to paste into `addVisionMeasurement`, not a single
number, because the error is strongly range- and count-dependent. For
calibration against current practice, AdvantageKit's published baseline is
`linearStdDev 0.02 m` and `angularStdDev 0.06 rad` at 1 m with one tag, scaled by
`distance^2 / tagCount`.

Measured at rest, and **labelled as such**. photontune's README makes the same
point about itself: *"every plateau it can find is a plateau in the one condition
that does not matter."* A stationary noise floor is a floor, not the operating
figure, and the report says so rather than implying otherwise.

Also emits the things that make the numbers trustworthy or not: ambiguity
distribution, tag count distribution, and the fraction of frames where the
multi-tag solve actually succeeded.

### 2. Continuous two-camera disagreement

Two cameras rigidly mounted have a fixed transform between them — on this rig
~51.3 mm and -29 degrees, measured. Each produces an independent pose. If the
transform between those poses changes by more than the noise floor from part 1,
**something moved**, and nothing else on the robot needs to be known to say so.

No fit, no baseline ritual, no routine, no condition matching. It runs during a
match. It is the only self-validating piece in the original proposal: two
independent paths to one conclusion.

This is the part that would have caught PhotonVision #2628 on this rig.

### 3. Vision-versus-gyro heading agreement — when a Pigeon exists

`psi + eps`, tracked over time. Newly worth doing because the audit found what
the superseded document had backwards: **Pigeon 2 yaw scale error is 0.8-1.7%**
— 3 to 6 degrees per robot rotation — measured by six independent teams, and
CTRE ships `GyroTrimConfigs` in units of degrees per rotation with a +/-180
range, which concedes the size of it. An auto accumulating two or three rotations
lands 10-15 degrees off heading.

The superseded document claimed a small scale deviation "must be" mechanical
tilt. Backwards: gain error is the default explanation and tilt is the exception,
since a 1.4% error would need about 9.6 degrees of tilt. Report both terms and
let the operator see which dominates.

Time-drift is negligible by comparison — the Pigeon 2 specification is 0.12
deg/hour at rest, so 15 seconds of auto is 0.1 degrees. Measure it once to
confirm the part is healthy, then stop worrying about it.

## Preconditions

1. photontune's structural baseline, in particular `inputImageRotationMode =
   DEG_0`. A non-zero rotation on the MIPI path corrupts the multi-tag pose by
   ~25 cm while the preview and published corners stay correct — PhotonVision
   #2628, open.
2. A camera calibration at the active resolution. Without one, and with
   `solvePNPEnabled`, PhotonVision publishes nothing at all.
3. For part 2 only: the camera-to-camera transform, from
   `mount/calibrate_mount.py`.

The layout does **not** need to be accurate. Part 1 measures spread, part 2
measures a change in a rigid transform, and part 3 is an angle difference.

## What is deliberately not built

Recorded so it does not get re-proposed:

- **Per-module drivetrain residual analysis.** No evidence the faults drift;
  invisible to the sensor that matters; names the wrong module half the time.
- **Absolute wheel radius.** AdvantageKit ships it. Re-run it rather than
  detecting that it moved, and re-run it on the carpet you will play on, since
  nap direction alone is worth up to 5%.
- **A roboRIO Java logger, the baseline/re-check protocol, the drift report
  format, the hold-heading strafe, the bidirectional spin.**
- **`delta - psi` as a product.** The maths is right and it is in the superseded
  document; the remedy for a small course bias is the same wherever it is
  applied, and the field contributes more.

## Build order

1. Part 1, against the wall tags. Needs the Pi only.
2. Part 2, which depends on part 1 for its threshold.
3. Part 3, when there is a Pigeon.

## Open

- Part 2's threshold is only as good as part 1's noise floor, and that floor is
  stationary. What it should be while driving is unmeasured.
- Whether two cameras 29 degrees apart are far enough apart for the disagreement
  figure to be sensitive. They want ~90 degrees for pose coverage anyway.
