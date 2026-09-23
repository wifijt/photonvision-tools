# Camera extrinsics and gyro mount yaw, measured against the tags

**Status: spec, not built. Rewritten 2026-09-23 after three independent
adversarial audits.** The first draft proposed a joint calibration of odometry,
the Pigeon and vision. Roughly half of it duplicated tools that already ship
free, and several of its headline parameters were not identifiable from the data
it proposed to collect. What follows is what survived, which is smaller, sharper,
and fits in a garage.

Target: swerve, Pigeon 2, CANcoder steer, Java/WPILib robot code, Python analyzer
on a laptop.

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

1. **`robotToCamera`, 6 DOF.** PhotonVision's pose-estimator documentation gives
   no guidance at all on obtaining this `Transform3d` — no procedure, no
   accuracy statement. Teams measure it off CAD or with a tape, and the error
   goes straight into every pose estimate as a bias that never averages out.
2. **Pigeon mount yaw relative to robot forward.** Gravity fixes pitch and roll;
   it leaves yaw free. Tuner X's calibration is gravity-based, so it cannot
   supply this one.
3. **Vision measurement standard deviations**, measured rather than guessed, in
   the form `SwerveDrivePoseEstimator` actually consumes, and as a function of
   range and tag count rather than a single number.
4. **Held-out validation as a practice.** Nobody in FRC does it, and it is the
   only thing separating a calibration from a plausible-looking fit.

---

## The gauge problem, which is the heart of this

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

### The resolution: declare the gauge

**Robot forward is a convention, not a measurement.** Pin `delta = 0` by jigging
the wheels against a straightedge when you set the CANcoder offsets — which is
what you do anyway — and *define* robot forward as the kinematics frame. Then
`psi = O1` and `eps = O2 - psi`, and both are determined.

The cost is honest and must be reported: **jig error goes directly into both**.
A 0.3 deg straightedge error is 0.3 deg on mount yaw and on camera yaw. The tool
states the assumed gauge on every report and treats jig accuracy as an input
uncertainty, not as zero.

The alternative — publishing only `psi-delta` and `psi+eps` and no individual
angles — is supported with `--no-gauge`, for anyone who does not trust the jig.

---

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

Refuses to run without all of these. Each has already produced a silent,
confident, wrong answer on this hardware.

1. **photontune's structural baseline asserted**, in particular
   `inputImageRotationMode = DEG_0`. A non-zero rotation corrupts the multi-tag
   pose by ~0.43 m while the preview and published corners stay correct. This rig
   ran with `DEG_90_CCW` set from the start, injecting that into every solve.
   See PhotonVision issue #2613.
2. **A camera calibration at the active resolution.** Without one and with
   `solvePNPEnabled`, PhotonVision publishes nothing at all — indistinguishable
   from a dead camera.
3. **A layout with per-tag uncertainty and co-visibility degree.** Does not exist
   yet: `solve_layout.py` currently emits only `ID` and `pose`. **This is a hard
   blocker and it is the first thing to build.**
4. **The gauge declared** — jig accuracy stated, or `--no-gauge`.
5. **Tuner X swerve setup and a tuned velocity loop** (SysId or AdvantageKit
   feedforward) must come first. A wrong nominal wheel radius in the *setpoint*
   is harmless — vision measures the true distance, and the separations only need
   the speeds to genuinely differ — but an untuned loop cannot hold a speed.

## The error budget

Three terms, and the first draft carried only one.

- **Layout scatter.** Per-tag survey noise. Improves with more survey data.
  Computed over the **run baseline**, not the summed path length, and with no
  `sqrt(N)` reduction across runs — it is the same layout every time, so the
  error is fully correlated.
- **Layout scale.** `solve_layout.py` takes `tag_size` as an argument and it is
  the *only* metric input to the bundle adjustment. **A 1% printing error scales
  the entire layout, every vision translation, and every length this tool
  reports.** Nothing in any routine can detect it; single-tag PnP cannot
  cross-check it because it uses the same number. **Measure a printed tag with a
  ruler and enter the real edge length.** This is the largest error in the system
  and the cheapest to eliminate.
- **Viewpoint-dependent pose bias.** `calibrate_mount.py` measured roughly
  +/-10 mm that "no amount of averaging at one viewpoint removes". It is
  systematic, comparable to the layout term, and **correlated with position along
  a run**, so it lands directly in whatever is being regressed against distance.
  Absent from the first draft entirely.

---

## Routines

### 1. Static — no enable, no joystick, no dead-man

Robot code, NetworkTables and `DataLogManager` all run **while the robot is
disabled**. This routine needs no Driver Station enable, nobody holding anything,
and no drivetrain at all.

Produces: vision noise floor; **vision standard deviations as a function of range
and tag count**, in the form `SwerveDrivePoseEstimator` consumes; timestamp
health check; Pigeon drift rate and mount tilt `phi`.

**Most of this needs no robot.** Everything except Pigeon drift is measurable
against wall tags today. It is the first deliverable and it ships alone.

It also states plainly what it is: a **stationary** noise floor. photontune's
README makes the same point about itself — *"every plateau it can find is a
plateau in the one condition that does not matter."* Static noise is not motion
noise, and the report says so rather than implying the number covers driving.

### 2. Spin — two turns each direction, slowly

Produces: `robotToCamera` bearing, radius and yaw (via `calibrate_mount.py`'s
shared-centre circle fit); cross-track camera translation; Pigeon mount yaw
against the declared gauge; `R_module / r_wheel` for the CAD cross-check.

Bidirectional because reversing `omega` cancels the `omega*dt` term. Not
optional.

### 3. Hold-heading strafe, two speeds — optional, needs a lane

Produces: the along-track camera translation and the differential timestamp
residual. **Skip it if you have no space** — the spin gives the rest, and this
routine's original purpose (wheel radius) has moved to the AdvantageKit routine.

### 4. Free drive — held out, and driven OUTSIDE the fitted envelope

Deliberately at a speed and heading the fitted runs did not visit. See below for
why "held out" alone is not enough.

---

## Validation, and why held-out residual is not sufficient

The first draft crowned held-out vision-vs-odometry residual. That is
**agreement, not accuracy**, and vision is the very thing the constants were
fitted to. Any error that moves vision and the constants together leaves the
residual near zero — layout scale, a frame-convention error, a systematic range
bias. This is the same mistake as optimising reprojection error, which photontune
already documents: *"it measures internal consistency of a fit, not pose
accuracy."*

Worse, the before/after framing **rewards** the failure. "Before" is CAD
nominals, so a fit that absorbs a 1% layout scale error into its constants shows
a *larger* improvement than a correct fit does.

Four things replace it:

1. **A parameter correlation matrix, published.** Any constant whose correlation
   with another exceeds a stated threshold is reported as **not separately
   determined** and is not printed as a pasteable number.
2. **Null-direction injection in the adversarial pass.** Perturb the parameters
   *along* a null direction and require the analyzer to answer "not observable"
   rather than a confident wrong value. A fit that cannot fail is not a
   measurement; a fit that cannot say *I cannot tell* is worse, because it prints
   a constant and somebody pastes it.
3. **A cross-method or physical-constraint check per parameter.** This is how
   `calibrate_mount.py` actually earned its accuracy — five checks, of which
   synthetic recovery was the *weakest*, and the decisive one was a physical
   constraint: a barrel length that proved a tape reading wrong, *"a physical
   constraint settled what 2500 frames could not."* Available here: a tape on the
   camera baseline; the rigid measured cam1->cam2 transform; a chord-and-radius
   prediction.
4. **Held-out drive outside the fitted envelope**, so a compensating parameter
   pair cannot predict it as well as the truth does.

**A fourth verdict exists: "your constants are fine; the problem is elsewhere."**
The measured history on this rig says the wins came from multi-tag, recalibration
and camera aiming — not constants. A calibration tool that cannot return a null
result will find a problem.

---

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

## Staleness: the part with no end date

A tool that emits pasted constants cannot tell anyone they have gone stale. New
wheels, a rebuilt module, a bumped camera.

**The two-camera disagreement monitor is the answer, and it is arguably a better
product than the calibration.** Two cameras with a rigid measured transform
(this rig: ~51.3 mm, yaw -29 deg) each produce an independent pose. Disagreement
beyond the static noise floor means something moved. No fit, no robot, no
routine — it runs continuously, during a match, and it catches the failure every
other part of this design is blind to.

Add: a versioned JSON schema and a tool that reads two records, so wheel radius
across a season is a tread-wear measurement rather than an anecdote. And Pigeon
bias moves with temperature, so a cold-gyro drift figure is not the match figure
— log die temperature with it.

---

## Build order

1. **`solve_layout.py` per-tag uncertainty and co-visibility degree.** The hard
   blocker. Independently fixes the 0.77 deg / 24 mm mount-transform bend already
   observed and attributed to a single-link tag.
2. **The static routine, standalone, against wall tags.** No robot, no enable.
   Ships on its own.
3. **The two-camera disagreement monitor.** No robot.
4. **Simulator and analyzer** for the spin — with the caveats below.
5. **Java logger and routines**, once a robot exists.
6. **Field validation.**

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

So the simulator stays, but its status changes: it is a **convention checker and
a null-direction prober**, not evidence of accuracy. Accuracy comes from item 3
of the validation list — a physical cross-check per parameter.

## Open

- **Repo.** This contains Java and is not a PhotonVision tool. It wants its own
  repo; the document lives here because moving a document is free.
- Whether the optional strafe routine is worth building at all once wheel radius
  is gone from it.
- Re-check photonlibpy's timestamp path against the version actually deployed.
