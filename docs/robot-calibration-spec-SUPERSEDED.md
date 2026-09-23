# SUPERSEDED — see vision-health-spec.md

**This document's conclusions are wrong. Kept for its degeneracy analysis, which
is not.**

Three adversarial audits in sequence killed the drivetrain half of this proposal:
there is no published evidence that steer offsets or module mounts drift at all
on modern hardware; the residual cannot see a misaligned CANcoder, returns a
uniform unattributable signal for the straightedge-against-a-pair case, flags all
four modules when the robot merely gains mass, and names the wrong module 48% of
the time on simulated robots; and the field itself contributes 2-12 inches
against a 1-2 inch target, which is an order of magnitude more than anything
measured here. The community's answer — tag-relative alignment instead of global
pose — was correct engineering, not a retreat.

What survived is in `vision-health-spec.md`. Evidence is in `docs/evidence/`.

Still worth reading here: the gauge argument (three yaw unknowns, two
observables, and why the free direction does not affect the robot), the
latency-versus-camera-translation separation and the trap in "drive it both
ways", and the closed-loop rule — a closed loop hides the error in the sensor it
closes on.

---

# What moved: drivetrain and camera drift detection

**Status: spec, not built. Rewritten 2026-09-23** after three adversarial audits
and a correction to the premise.

Target: swerve, Pigeon 2, CANcoder steer, Java/WPILib robot code, Python
analyzer.

---

## The problem this actually solves

The team does the rough calibration. They have to — Tuner X's swerve generator
will not finish without CANcoder offsets, and the wheels get zeroed with a
straightedge held against them. That is the baseline, and this tool does not
replace it.

**Then the robot drives.** Bolts loosen, a module shifts on its mounts, a
CANcoder creeps, a camera gets knocked in the pit. Nothing announces any of it.
The robot yaws slightly off course, vision starts fighting odometry, and three
weeks go into an auto routine built on measurements that stopped being true
after the first hard practice session.

So the question is not "what are these constants" — the team already answered
that, adequately, once. The question is **"what has moved since?"**

### Why that is a much easier problem

Every systematic error the audits identified as fatal to an absolute calibration
is **common mode between two runs, and cancels in the difference**:

| error | absolute calibration | drift detection |
|---|---|---|
| layout scale (a 1% mis-printed tag) | 1% error in every length | cancels |
| per-tag survey scatter | a floor under everything | cancels if the tags do not move |
| viewpoint-dependent pose bias, ~+/-10 mm | systematic, correlated with the regressor | cancels at the same viewpoint |
| straightedge / jig error | imported directly | cancels — it is in the baseline too |
| eyeballed wheel radius | wrong by up to 1/4 inch | cancels; the *change* is what matters |

What is left is **precision**, and precision is the one thing these fits deliver
well. The layout does not have to be accurate. It has to be *stable*, and tags
screwed to a wall are stable.

This is why the tool is worth building even though the absolute version was not:
it needs no survey uncertainty, no gauge, no straightedge, and no truth.

---

## What this does NOT build, and why

Naming the prior art first, because the first draft did not and that was its
largest error.

| | who already does it | what is left for us |
|---|---|---|
| **effective wheel radius** | **AdvantageKit swerve templates** ship a Drive Wheel Radius Characterization auto routine: spin in place one rotation, print a radius. Same carpet caveat. No tag survey, no lane. | use it. Optionally cross-check against our spin. Nothing to build. |
| **kS / kV / kA** | WPILib SysId, and AdvantageKit's feedforward routine | nothing. It is a **prerequisite**, not a competitor — see below. |
| **Pigeon pitch/roll mount** | CTRE Tuner X mount calibration, gravity-based | nothing |
| **steer direction / drive direction sanity** | Tuner X swerve validation | nothing |
| **vision capture timestamp** | PhotonVision timestamps frames at start-of-exposure from the V4L2 buffer and photonlib carries them into roboRIO time | **a health check, not a constant** — see below |

Dropping the wheel-radius fit removes the requirement that made this a gym job:
a clear 6-8 m lane with tags visible throughout. The remaining routines run in a
garage.

## What is actually missing from FRC

Nothing in FRC tells a team that the robot has drifted out of the calibration it
started the season with. Tuner X, SysId and AdvantageKit all *establish* values;
none of them watch whether the values are still true.

1. **Per-module steer, position and radius drift from the discarded kinematics
   residual.** No vision, no tags, no field, no routine — it runs on ordinary
   driving. This is the core of the tool. See below.
2. **`delta - psi` — the robot's course error.** "It yaws left slightly", as a
   number, tracked over time.
3. **`psi + eps` — vision versus gyro heading agreement.** "Vision fights
   odometry", as a number.
4. **`robotToCamera` change.** PhotonVision's own docs give no procedure for
   obtaining the `Transform3d` at all, let alone for noticing it has moved.
5. **Vision measurement standard deviations**, measured rather than guessed, in
   the form `SwerveDrivePoseEstimator` consumes, by range and tag count.

---

## The gauge problem, and why drift detection sidesteps it

**You cannot measure the angle between the chassis and itself.** Three unknowns
are all "a rotation between the body frame and something else":

```
psi  Pigeon mount yaw          delta  common steer zero bias
eps  robotToCamera yaw
```

Motion against vision yields only **two** first-order observables:

```
O1 = odometry travel direction - vision travel direction  =  psi - delta
O2 = gyro heading             - vision heading            =  psi + eps
```

Two equations, three unknowns, with an exact null direction
`(psi, delta, eps) -> (psi+a, delta+a, eps-a)`. Both observables are invariant
along it. The first draft printed all three as independently measured. They are
not.

Verified against WPILib's own `SwerveDriveOdometry`, which takes heading from
the gyro and only *translation* from the module deltas:

```
clean                 odo travel dir  +0.0000 deg   odo heading  +0.0000
steer bias +2 deg     odo travel dir  -2.0000 deg   odo heading  +0.0000
gyro mount yaw +2     odo travel dir  +2.0000 deg   odo heading  +2.0000
steer -2 & gyro +2    odo travel dir  +4.0000 deg   odo heading  +2.0000
```

The escapes are closed. It holds under strafe too — a common steer bias is a
pure body-frame rotation for *any* translation, so no driving direction breaks
it. And the spin does not break it either: `delta` enters the drivetrain as
`cos(delta)`, which at 0.4 deg is 24 ppm, far under the spin's own scatter.

### The resolution: the free direction does not matter

An earlier draft said to pin `delta = 0` with a straightedge against the wheels
and call that robot forward. **That is wrong, and it is wrong in the way that
matters most** — the straightedge is a slightly bowed board, held up by eye and
taken away before the wheel is nudged. Defining the frame that way imports the
very error the tool exists to find.

It is also unnecessary. Work out what the robot actually does. Commanded
field-relative velocity `v_f`; the code converts through gyro heading
`theta_g = theta_true + psi`; the modules physically point `delta` off what
kinematics believes. The velocity the robot really travels at is

```
v_physical = R(theta_true) R(delta) R(-theta_true - psi) v_f  =  R(delta - psi) v_f
```

**The course error is `delta - psi`, which is exactly `-O1`.** Under the gauge
shift, `delta` and `psi` move together and it cancels. The same holds for vision:
what matters is `psi + eps = O2`.

So **both functionally relevant combinations are observable, and the
unobservable direction has no effect on the robot.** There is nothing to pin and
no board to trust. The tool reports:

- `delta - psi` — how far off course the robot drives when told to go straight,
  field-relative. This is "the robot yaws left ever so slightly", as a number.
- `psi + eps` — whether vision and the gyro agree about which way the robot
  faces. This is "vision fights odometry", as a number.

Where you apply the correction is free: fold it into the CANcoder offsets, the
gyro offset, or `robotToCamera`. All three are equivalent for pose, because that
is precisely the free direction. Folding it into the CANcoder offsets is
preferred only because it leaves the wheels pointing where the code thinks they
point, which is easier for the next person to reason about.

---

## What the drivetrain can see by itself, and what it cannot

Four modules report 8 numbers. The chassis has 3 degrees of freedom.
`SwerveDriveKinematics.toChassisSpeeds` least-squares them to 3 and discards the
other 5 every loop. Some faults live in those 5. **Not all of them, and not the
one that matters most.**

### The rule: a closed loop hides the error in the sensor it closes on

The steer controller drives the **reported** CANcoder angle to the commanded
angle. So the reported angle always equals the command, whatever the magnet
offset is, and the physical misalignment leaves **no trace** in the module state.
The drive encoder then measures only the component of the module's velocity along
the wheel's actual heading — the perpendicular part is scrub, and nothing
measures scrub. What survives is a `cos(delta)` deficit on the reported speed.

Measured, physically honest harness in
`docs/evidence/swerve_residual_physical.py`:

```
fault                          translate 2 m/s    spin 2 rad/s
wheel radius -2%                  0.025495          0.008485
module mounted 15 mm off          0.000000          0.010607
CANcoder off 1 deg                0.000194          0.000065
```

`1 - cos(1 deg) = 1.52e-4`, times 2 m/s, is `3.0e-4`. That is the entire signal.
**A mis-zeroed CANcoder is not detectable from the drivetrain alone** — and it is
the most common real fault, because the wheels are zeroed with a slightly bowed
board held up by eye.

An earlier draft of this document claimed otherwise, from a test that perturbed
the reported angle while leaving the reported speed at full magnitude. That is
not a state the hardware can produce. The retraction is recorded in the evidence
file rather than deleted.

### What the residual does catch

- **Per-module drive scale / wheel radius.** No loop corrects distance-per-
  rotation, so the error stands in the reported state. Scales with speed.
- **Module mounting position.** An error in the *model*, not in a sensor —
  nothing closes a loop on where a module is bolted. **Exactly zero under pure
  translation** and scaling with `omega`, which makes it cleanly separable from
  everything else.

Both need no vision, no tags, no field and no routine — ordinary driving is
enough. Both are *relative*: a fault shared by all four modules is as invisible
here as a common steer bias, and absolute scale still comes from AdvantageKit's
spin routine or from vision.

### What a mis-zeroed CANcoder needs instead

Something **outside the steer loop**:

1. **Vision — the reliable one.** It measures the course the robot actually took,
   which is the `delta - psi` observable. This is why the vision half of the tool
   is not optional and cannot be deferred.
2. **The gyro**, which is independent of every module. A fighting module drags
   the chassis off course through tyre scrub and force balance — real, but a
   force-balance effect that ideal kinematics does not model, so its magnitude
   cannot be predicted here and must be measured on hardware.
3. **Steer motor current — a hypothesis, untested.** A misaligned module is held
   against a lateral scrub load, so its steer motor should draw more at steady
   state, and that is free on the CAN bus. Physically sound; no measurement
   behind it. Worth an hour on a real robot before it is believed.

## The remaining degeneracies, and what breaks each

### Latency vs. camera translation — and the trap in "both directions"

At constant velocity a timestamp error `dt` displaces the vision-implied pose by
`v*dt` **along the field velocity**; a camera mount error displaces it by a
vector **fixed in the body frame**. Two experiments both sound like "drive the
path both ways" and only one works:

- **Strafe back with the heading HELD.** Body frame unchanged, field velocity
  reversed. The latency term flips sign; the mount term does not. **Separates.**
  A swerve can do this. This is the routine.
- **Turn 180 deg and drive back.** Velocity reverses *and* the body frame rotates
  with it, so both terms flip together. **Separates nothing.**

The second is the natural reading of the phrase and what a driver does by
default. It must be stated as a hold-heading strafe or the routine is worthless.

Both runs must share **one** pose anchor. Re-anchoring per run absorbs exactly
the offset being measured.

**Cross-track camera translation is not observable here at all.** During a
constant-heading run `dR/dt = 0`, so a lateral mount offset is a constant
body-frame offset, indistinguishable from initial-pose registration in both
directions at every speed. Cross-track translation comes only from the spin,
whose whole mechanism is that `dR/dt != 0`.

### Camera bearing vs. omega*dt in the spin

A timestamp offset during a spin rotates the camera's measured position around
the circle by `omega*dt` — degenerate with an error in the camera's mount
bearing at a single rotation rate. At 4 turns in 120 s, `omega = 0.209 rad/s`,
and a 20 ms offset is **0.24 deg**, larger than the precision the fit will
otherwise claim.

**Reversing the spin flips `omega*dt` and leaves the bearing put.** This is why
the spin is bidirectional. It is load-bearing, not a symmetry nicety.

### Pigeon yaw scale vs. Pigeon mount tilt

A Pigeon whose z-axis is off vertical by `phi` measures `omega*cos(phi)` for
planar rotation — an exact scale factor. A reported "yaw scale" of 0.99934 is
`cos(2.08 deg)`, which is a shim under one bolt, not a sensor gain error, and is
far outside a Pigeon 2's actual gyro scale spec.

**Break:** the accelerometer at rest gives `phi`. Report
`yaw scale = cos(phi_measured) x g_sensor` so the operator sees which term
dominates, instead of being invited to "fix" a gain that is really mechanical.

### Wheel radius vs. module radius (informational only)

Kept because the cross-check is still worth printing even though we no longer
fit wheel radius ourselves.

Spin encoder counts go as `omega * R_module / r_wheel`, so only the **ratio** is
identified — and the first draft had the sign wrong. A small *wheel* radius
drives counts **up**; a small *module* radius drives them **down**. One
masquerades as the other only when their errors have **opposite** signs.

A pure translation involves no `R_module`, so a straight run measures `r_wheel`
alone. Comparing that against the spin's ratio checks the module translations
against CAD. At a +/-6 mm layout, a 2% module error on a 0.4 m drive base is
about 8 mm — comparable to the layout floor — so this check declares a detection
threshold and reports **not measured** below it rather than printing a number.

---

## Vision timestamps: a health check, not a constant

PhotonVision timestamps frames at **start of exposure** from the V4L2 buffer and
photonlib carries that into the roboRIO's timebase through its TimeSync
client/server pair. There is no constant to paste — the documented flow sends
`result.getTimestampSeconds()` straight into `addVisionMeasurement`, and there is
no WPILib or photonlib parameter that would accept an offset.

So the tool fits a residual and **expects zero**. A residual of more than a few
milliseconds is a fault report, not a measurement:

- is the roboRIO the NT server?
- what is `metadata.timeSinceLastPong`? (photonlib warns past 5 s)
- do the PhotonVision and photonlib versions match?
- is the analyzer reading `captureTimestampMicros`, or accidentally the DataLog
  record timestamp? (see Logging)

**Note the Java/Python split.** The Java path uses the synced clock. photonlibpy
2026.0.1 does not — `getTimestampSeconds()` computes
`ntReceiveTimestampMicros - (publishTimestampMicros - captureTimestampMicros)`,
carrying an explicit `TODO - we don't trust NT4 to correctly latency-compensate`,
which leaves the network transport term uncorrected. Verified by reading the
installed package; **this was 2026.0.1 on a laptop, not the 2026.3.4 on the
coprocessor**, and should be re-checked against the deployed version.

The residual is still what contaminates the `robotToCamera` x-estimate, so the
hold-heading strafe stays whether or not the residual turns out to be zero.

And the encoder side is not free of latency either. Values logged at roboRIO
time `T` describe the drivetrain at `T - tau_can` — status-frame period plus
sample age. Both terms scale with `v`, so what is fitted is a **differential**
offset, `dt_vision - tau_odometry`. A negative value is not a bug. The Phoenix
status-frame configuration is recorded with the calibration, because changing it
invalidates the number.

---

## Preconditions

Drift detection compares two runs, so the requirement is **sameness**, not
accuracy. Each item below has already produced a silent wrong answer on this
hardware, or would silently break a comparison.

1. **The team's rough calibration is done.** Tuner X swerve setup, CANcoder
   offsets, a tuned velocity loop. This is an input, not a competitor — the tool
   measures departure from it and cannot run before it exists.
2. **The pipeline configuration is identical between runs.** photontune's
   structural baseline asserted, in particular `inputImageRotationMode = DEG_0`.
   A rotation change between baseline and re-check masquerades perfectly as
   camera drift, and a non-zero rotation corrupts the multi-tag pose by ~0.43 m
   while the preview and published corners stay correct — this rig ran that way
   from the start. See PhotonVision issue #2613.
3. **A camera calibration at the active resolution**, unchanged between runs.
   Without one, and with `solvePNPEnabled`, PhotonVision publishes nothing at
   all — indistinguishable from a dead camera. Recalibrating a camera between
   runs invalidates the comparison; record the calibration's identity.
4. **The same layout, and it has not moved.** Recorded by content hash. The
   layout does **not** need to be accurate — errors in it cancel — but comparing
   across two different layouts is meaningless, and the tool refuses.
5. **Repeatability established.** Three back-to-back baselines without touching
   the robot. Until that exists there is no threshold and nothing can be flagged.

Note what is *no longer* required, and was in the previous draft: per-tag layout
uncertainty, a declared gauge, and a straightedge. All three were needed only for
absolute numbers.

## The error budget, and what it costs to measure a change

Three terms, and what matters is how each behaves **between two runs**.

- **Common mode — cancels.** Layout scale, per-tag scatter, viewpoint bias, jig
  error, nominal wheel radius. All of it, provided the tags do not move and the
  re-check is taken from comparable viewpoints. This is the whole reason the
  tool works.
- **Repeatability — the real limit.** A drift threshold is meaningless without
  knowing the spread of the measurement itself. **The tool establishes its own
  repeatability before it is allowed to flag anything**: take the baseline three
  times, back to back, without touching the robot, and the scatter across those
  is the floor. Anything smaller than it is not a detection.
- **Condition drift — the thing that fools it.** Carpet vs concrete, battery
  voltage, tyre temperature, and the viewpoint the re-check was taken from. These
  do not cancel, and they are the likely source of a false alarm. Log them, and
  refuse to compare runs whose conditions differ beyond stated bounds.

Absolute accuracy is **out of scope**. When the tool says a module moved 1.4 deg,
it means 1.4 deg *relative to the baseline*, not 1.4 deg from true. That is what
the mechanic needs to know, and it is what can honestly be claimed.

## Routines

### 0. Continuous — no routine, no enable, no operator

Per-module residuals computed every loop from whatever the robot is already
doing. Practice, a match, driving it onto a cart. No tags, no vision, no space,
nobody holding a button. This is the product; everything below is supporting.

Output is a per-module time series, and the useful view is its **trend**: a
module whose residual was flat for three weeks and has climbed since Tuesday has
a loose bolt, and the mechanic is told which module and which axis.

### 1. Static — no enable, no joystick, no dead-man

Robot code, NetworkTables and `DataLogManager` all run **while the robot is
disabled**, so this needs no Driver Station enable and no drivetrain.

Produces: vision noise floor; **vision standard deviations by range and tag
count**, in the form `SwerveDrivePoseEstimator` consumes; timestamp health check;
Pigeon drift rate and mount tilt `phi`; and the two-camera disagreement figure.

**Most of it needs no robot** — everything except Pigeon drift is measurable
against wall tags today. It ships first, alone.

It states plainly what it is: a **stationary** noise floor. photontune's README
makes the same point about itself — *"every plateau it can find is a plateau in
the one condition that does not matter."*

### 2. Spin — two turns each direction, slowly

Produces: `delta - psi`, `psi + eps`, `robotToCamera` bearing, radius and yaw
(via `calibrate_mount.py`'s shared-centre circle fit), and cross-track camera
translation.

Bidirectional because reversing `omega` cancels the `omega*dt` term. Not
optional.

**Taken from the same marked spot each time.** Viewpoint bias is common mode only
if the viewpoint is common. Mark the floor.

### 3. Hold-heading strafe — optional, needs a lane

Along-track camera translation and the differential timestamp residual. Skip it
if you have no space; the spin gives the rest.

### Baseline and re-check

A **baseline** is routines 1 and 2 run three times back to back, right after the
team's rough calibration, with conditions logged. A **re-check** is the same
routines under comparable conditions. The report is the difference, and the
scatter across the three baseline repeats is the threshold that decides whether
a difference means anything.

---

## Validation: the threshold has to be earned before anything is flagged

An absolute calibration has to prove it is *right*. A drift detector has to prove
it is *quiet* — that it does not cry wolf when nothing has changed — and then
that it fires when something has.

Four things, in order:

1. **Repeatability, measured.** Three back-to-back baselines with the robot
   untouched. The scatter across them is the noise floor, and it *is* the
   detection threshold. A tool that reports a 0.3 deg change with a 0.5 deg floor
   is reporting nothing.
2. **The null run.** Re-check on a different day, different battery, same robot,
   nothing touched. **It must report no change.** This is the test that matters
   most, because a drift detector's characteristic failure is a false alarm that
   sends a mechanic to tighten a bolt that was already tight — and having done
   so, they will never trust it again.
3. **Physical sabotage, which transfers cleanly here.** This is the one place
   photontune's approach carries over intact: loosen a specific module, shim a
   camera by a known angle, deliberately mis-set one CANcoder by 2 deg. Ground
   truth is a real thing done to real hardware, external to whoever wrote the
   analyzer. Then check it names the right module and the right axis. That is
   falsification, not a gradient check.
4. **Cross-method agreement where it is free.** The two cameras have a rigid
   measured transform; if one has moved, the vision fit and the camera-to-camera
   disagreement must say so together. Two independent paths to the same
   conclusion is worth more than either alone.

Note what is *not* on this list, and was the previous draft's headline:
**held-out vision-vs-odometry residual**. It measures agreement, not accuracy,
and vision is what the parameters were fitted to. For drift detection it is worse
than useless, because a compensating pair drifting together leaves it flat.

## Safety

**Cancelling a command does not stop a motor.** It stops writing new values; a
CAN motor controller holds its last output until told otherwise. The first draft
said "letting go stops it", which is false and is the most likely way this tool
hurts someone.

- Every routine implements `end(boolean interrupted)` with an unconditional
  `drivetrain.stop()` — zero volts, not zero setpoint. Mandatory, not delegated
  to a default command.
- The drivetrain has a default command and it commands zero with no input.
- `.withInterruptBehavior(kCancelIncoming)` so nothing steals the drivetrain
  mid-routine. `whileTrue` will not reschedule a routine that was interrupted
  while the trigger is still held — "held means running" is false without this.
- `MotorSafety` enabled on drive outputs during routines.
- **Teleoperated mode, never Practice.** Practice mode cycles match timings and
  will auto-disable partway through a long routine.
- The dead-man is **not** laptop-independent: the controller plugs into the
  Driver Station, so it is a network signal at the DS packet period. Claiming
  otherwise was wrong. It is still a good layer; it is not the only one.
- The operator is pinned to the laptop and cannot see the far end of a long run.
  Another reason routine 3 is optional.

## Logging

Vision and drivetrain both logged on the roboRIO via `DataLogManager`. The
first draft's rationale — avoiding network clock skew — was wrong, since
photonlib already corrects into roboRIO time. The real reasons stand: full rate,
and it survives a wifi dropout.

Four traps, all of which produce plausible wrong numbers rather than errors:

- **The USB stick is silently optional.** No stick, or a stick formatted exFAT or
  NTFS, and logging falls back to `/home/lvuser/logs` — where, under 50 MB free,
  WPILib **deletes** old logs while you are writing. FAT32, 32 GB or smaller. The
  logger asserts `DataLogManager.getLogDir()` is on the USB path and **refuses to
  arm a routine** otherwise.
- **The DataLog record timestamp is not the capture timestamp.**
  `DataLogManager.start()` auto-logs NT changes, so PhotonVision's raw topic
  lands with an NT *receive* time — jittery, loop-phased, and exactly the thing
  that would manufacture a bogus tens-of-milliseconds "latency". The analyzer
  reads `captureTimestampMicros` and nothing else.
- **Results are raw bytes in PhotonVision's own serialization**, version-coupled
  to the build. The Java logger decodes each result and writes **plain scalar
  entries** — capture timestamp, tag IDs, pose components, ambiguity,
  `timeSinceLastPong`. Then the Python side needs no PhotonVision decoder at all.
- **`DriverStation.startDataLog(DataLogManager.getLog())`.** Without it the
  analyzer cannot tell a clean run from one disabled mid-lane by a comms dropout,
  and will happily fit the coast-down.

**The 20 ms loop is not the resolution limit** and the first draft implied it was.
Each result carries its own start-of-exposure timestamp, and
`getAllUnreadResults()` returns every queued result rather than the newest — so
the loop rate controls how often the queue is drained, not timestamp resolution.
Using `getLatestResult()` instead discards most frames *and* couples the
timestamp to loop phase, which is the only way the 20 ms figure becomes real.
On this stack Phoenix 6's `SwerveDrivetrain` runs odometry on its own thread at
250 Hz (CAN FD) / 100 Hz (CAN 2.0); sample the drivetrain state **at** each
vision timestamp from the interpolating buffer, not at the nearest loop tick.

**Log `RobotController.getBatteryVoltage()` and the battery identity per run.**
Four routines plus a re-run is more than one battery, and since the separations
rely on speeds genuinely differing, sag that changes speed between runs
contaminates exactly what the design rests on. Reject any run whose measured
speed drifted beyond a stated tolerance.

---

## Output

Not constants to paste. **A diff, with a part number.**

```
── drift report ──  2026-11-02 18:40   vs baseline 2026-09-28 (3 repeats)
   layout  shop-wall-2026-10-02  (hash match)     pipeline config: match
   camera calibrations: match      threshold: 3x baseline scatter

DRIVETRAIN          from 41 min of ordinary driving, no routine
   module     position       radius
   FL           -           -0.05%        within noise
   FR           -           +0.03%        within noise
   BL           -           -0.02%        within noise
   BR         0.8 mm        -0.71%      ** CHANGED **
        threshold 0.9 mm / 0.24%  (3x baseline scatter)
        grew over 6 sessions, not a step.
        -> CHECK THE BR MODULE'S FRAME BOLTS AND TREAD.

   THERE IS NO STEER COLUMN HERE, and there cannot be. The steer loop closes
   on the CANcoder, so a misaligned one leaves no trace in the module state -
   0.000194 at 1 deg, which aliases onto the RADIUS column as a cos(delta)
   speed deficit, not onto a steer column. An earlier version of this sample
   printed "BR +1.41 deg ... signature is steer-dominant" and that number was
   impossible. See docs/evidence/swerve_residual_physical.py.

COURSE ERROR
   delta - psi        baseline -0.21 deg    now -1.58 deg    ** CHANGED **
        the robot now drives 1.37 deg left of commanded, field-relative.
        consistent with the BR steer drift above - expect this to go away
        when BR is fixed. Recheck before changing anything else.

VISION
   psi + eps          baseline +0.04 deg    now +0.07 deg    within noise
   cam0 -> cam1       baseline  51.3 mm / -29.01 deg
                      now       51.2 mm / -28.97 deg        within noise
        both cameras agree with each other and with the gyro. Nothing moved.

CONDITIONS
   carpet, 11.9 V mean (baseline 12.1), spin taken from the marked spot.

VERDICT: one mechanical fault, BR module. Vision is clean. Do not re-zero
         the other three modules - they have not moved.
```

Three things it does on purpose.

**It names a part, not a parameter.** "BR steer +1.41 deg" is a number; "check the
BR steer pulley and CANcoder clamp bolts" is an action. The tool exists to send
somebody to the right corner of the robot with the right wrench.

**It distinguishes a step from a trend.** A bolt working loose over six sessions
looks different from a collision, and the fix is different too.

**It says what has *not* moved, explicitly.** The failure mode of a drift report
is a team re-zeroing all four modules because one was flagged. Saying "these
three have not moved" is as important as the finding.

## Build order

The reframe moves most of the work forward. Nothing here is blocked on the layout
uncertainty that blocked the previous draft, because drift detection does not
need an accurate layout.

1. **The per-module residual analyzer.** Testable with no Pi, no robot and no
   tags against WPILib's own kinematics — but note that this validates the
   *software*, not the hardware's behaviour, and it covers only **wheel radius
   and module mounting position**. The correct evidence is
   `docs/evidence/swerve_residual_physical.py`; the earlier
   `swerve_residual_observability.py` is SUPERSEDED and its headline number is
   wrong. **This is no longer "the core product"** — that claim rested on
   detecting CANcoder misalignment, which it cannot do.
2. **Baseline / re-check schema and the diff report.** Versioned JSON, condition
   logging, content hashes, the repeatability threshold. Pure Python.
3. **The static routine**, standalone against wall tags. No robot, no enable.
   Gives the vision noise floor, the stddevs, and the two-camera figure.
4. **The two-camera disagreement monitor**, continuous. No fit, no robot.
5. **The Java logger** — per-module states, gyro, decoded vision scalars,
   battery, conditions — once a robot exists.
6. **Physical sabotage validation** on the real robot: loosen a known module,
   shim a camera, mis-set one CANcoder.

### On simulator-first

The first draft claimed steps 1-3 were "most of the work" and justified it with
`calibrate_mount.py`'s *"synthetic ground truth recovered exactly."* That is the
first of **five** checks in that docstring and the weakest; the other four are
cross-method or physical-constraint, against things the author did not write.

A simulator and an analyzer written by the same person share a mental model: the
swerve kinematics, the sign conventions, the tag-frame convention, the meaning of
a capture timestamp. Every one of those has already bitten this project and none
was caught by reasoning — the tag-frame rotation was found by brute-forcing all
24 proper rotations, and the ntcore clock bug was found only against real data.
Recovering an injected 23 ms from your own simulator proves nothing about
whether the roboRIO logs what you think it logs.

Nor does sabotage transfer cleanly. photontune's sabotage breaks **real**
PhotonVision settings on **real** hardware and checks a **real** repair; ground
truth is external to the author. Simulator sabotage perturbs a model parameter
and checks the estimator of that same model notices — a gradient check, blind by
construction to scrub, slip, module skew, CAN staleness and the viewpoint bias.
And the record is not encouraging: DEFECTS.md notes that the real-hardware
verdict matrix already missed **four deliberate reversions of the headline
fixes, all green**.

Drift detection changes this favourably. Step 1's ground truth is **WPILib's own
kinematics**, not a model written here — perturb a module in `SwerveDrive4Kinematics`
and require the analyzer to name it. And step 6's sabotage is **physical**, on real
hardware, exactly as photontune's is. The simulator's remaining job is convention
checking and null-direction probing, which is what it is good for.

## Open

- **Repo.** Contains Java, not a PhotonVision tool. Wants its own repo; the
  document lives here because moving a document is free.
- **How much ordinary driving is enough** for a per-module residual estimate to
  beat its own noise floor. Unknown until measured; it sets whether this is a
  per-session or per-week report.
- **The precondition may disqualify the motivating case.** This document requires
  a good baseline to measure departure from — and the team that motivated it
  (bowed board, CANcoder zeroed by eye, radius to the nearest quarter inch,
  "accepted as good enough") is precisely a team that never had one. Drift
  detection tells them nothing. What they needed was a good *first* measurement,
  which this document explicitly declines to provide. This is unresolved and it
  may be fatal.
- Re-check photonlibpy's timestamp path against the deployed version.
