# Known defects and traps, the conditions that trigger them, and what to do

Everything here was found by forcing the failure on real hardware, not by
reading code. Where a thing is unmeasured, that is said.

Fixed entries stay. The *trap* outlives the fix, because the next tool written
against NetworkTables will meet it again, and the whole point of §1 is that it
is invisible from the code.

Ordered by whether it can bite you at a competition.

---

## 1. An unsubscribed NT4 client reads every server as empty

**Condition.** Any code that connects to NetworkTables and then lists what is
there — `getSubTables()`, `getTopics()` — without first subscribing.

**What happens.** Nothing. Literally: an empty list, from a server that is
publishing hundreds of topics. NT4 announces a topic only to clients that have
SUBSCRIBED to it, and the listing is computed from the client's own store of
announcements. Measured against a roboRIO with two cameras publishing 245
results each per 3 seconds:

```
require_nt returned: connected=True after 0.21s
unsubscribed, immediately   -> []
unsubscribed, +0.5s         -> []
unsubscribed, +1.0s         -> []
unsubscribed, +2.0s         -> []
subscribed,   +0.3s         -> Left, Right, coprocessors, .timesync
```

**It is not a race.** Waiting does not help and never would have. `record()`
carried a `time.sleep(1.5)` commented "let the topic list arrive", which could
not work and, worse, made the real fix look as though it had been tried.

**Cost when it bit.** `calibrate_mount` could not pass its own camera check
against any rig, ever. An evening went into proving a Pi was healthy — it was —
because the error said "Nothing at all is published under /photonvision/" and
that was a statement about the client, not the server.

**What to do.** Use `preflight.nt_subtables()` or `preflight.nt_top_level()`,
which open a `MultiSubscriber` on `/`, poll until names arrive, and close it.
Never call `getTopics()` or `getSubTables()` on a freshly connected instance.

Measured follow-up: once something has subscribed, the topic list SURVIVES
that subscriber being closed — 416 topics still listed 3 s after `close()`.
So later calls in the same process work. That is why `_gyro_candidates` works,
and it is luck rather than design; it is commented as such.

**Fixed** in `db6dc41`.

---

## 2. A camera table can exist, accept your name, and deliver nothing

**Condition.** Anything ever constructed a `PhotonCamera` with a name the
coprocessor does not have — a typo, the wrong case, an old robot build.

**What happens.** Constructing a `PhotonCamera` PUBLISHES
`driverModeRequest`, `fpsLimitRequest` and `pipelineIndexRequest` under
whatever name it was given. Nothing removes them while that publisher is
connected. So a convincing `left` sits beside the real `Left` for the life of
the NT server, and asking for it SUCCEEDS and then records nothing — which is
worse than a clean error. Measured on a working rig:

```
Left     22 topics: cameraIntrinsics, hasTarget, heartbeat, fps, ...
Right    22 topics: cameraIntrinsics, hasTarget, heartbeat, fps, ...
left      3 topics: driverModeRequest, fpsLimitRequest, pipelineIndexRequest
right     3 topics: driverModeRequest, fpsLimitRequest, pipelineIndexRequest
```

**What to do.** Trust `/photonvision/coprocessors/<MAC>/cameraNames` — the
coprocessor stating what it has — over anything enumerated from subtables.
`preflight.discover_cameras()` does this and names the leftovers; `preflight.py`
and `calibrate_mount` both print them with an explanation.

**Not fixable here.** The tables are real and the NT server holds them until it
restarts. On a robot the NT server is the robot program, so they clear on a
roboRIO reboot — and come straight back if the thing creating them is the robot
code itself. Find the publisher rather than waiting it out.

---

## 3. `--gyro-topic` could not read a struct heading

**Condition.** A robot that publishes heading as `struct:Rotation2d` — which is
what AdvantageKit does, so it is the common case, not the exotic one.

**What happens.** `getDoubleTopic()` on a struct topic returns its default
forever. Measured against a live robot: `nan` from the double subscriber while
the raw topic handed back 8 good bytes. The tool then reported the topic "is
not publishing yet", which is an accusation aimed at robot code that is fine.

Compounding it, the candidate list filtered to numeric types, so on that robot
the only thing it could offer was `YawVelocityRadPerSec` — a RATE where a
heading is wanted. Suggesting a rate is worse than suggesting nothing.

**What to do.** Nothing; `_GyroReader` handles `struct:Rotation2d` and
`struct:Pose2d` (angle at byte 16) and says which it is. `Pose3d` carries a
quaternion and is not handled — pass the `Rotation2d` topic, not the pose.

**Fixed** in this commit. Verified against the live robot both ways.

---

## 4. Camera names were hardcoded, and rotted

**Condition.** Running `calibrate_mount` without `--cameras` after the cameras
were renamed in PhotonVision.

**What happens.** The default was `OV9281,OV9281 (1)` — the nicknames
PhotonVision generates from the sensor model. Once the cameras became `Left`
and `Right`, the tool's own default could not pass the tool's own check, and
the error named the cameras rather than the stale default.

**What to do.** Omit `--cameras`. It reads them from the coprocessor.

**Fixed** in `4e9e39b`.

---

## 5. `preflight.py` had no verdict

**Condition.** Running it at all.

**What happens.** It printed a table of OK / UNREACHABLE lines and exited 0
whichever they said. "Preflight ran and passed" could therefore be said of a
run whose every line read UNREACHABLE. A TCP connect to 5810 also succeeds
against an NT server with nothing whatsoever on it, so even an all-OK run
proved less than it appeared to.

**What to do.** Nothing; it exits non-zero on failure and now performs the NT
publish check itself, printing the coprocessor, its cameras and any leftovers.

**Fixed** in `db6dc41`.

---

## 6. macOS: a second link-local route can swallow the robot network

**Condition.** A Mac on wifi AND on the robot network by USB Ethernet, talking
to a device that fell back to a `169.254.x.x` address.

**What happens.** Both interfaces carry a `169.254/16` route and the wifi one
wins. Traffic leaves by the wrong interface and vanishes. Ports that are wide
open read as closed, pings fail, and the device looks dead. Measured: a roboRIO
reported every port closed and no robot code running; it was serving NT, a web
dashboard and SSH the whole time, and `ping -S <local-link-local>` reached it
in 0.95 ms.

**What to do.** Force the source address before believing any negative result:
`ping -S 169.254.x.y <target>`, `nc -s 169.254.x.y ...`. Better, give the
device a static address on the robot subnet so link-local never comes up.

**Not ours to fix.** Environment.

---

## 7. The Pi has no RTC battery and no `fake-hwclock`

**Condition.** A power cut.

**What happens.** The clock comes back wrong, and every timestamp this
repo writes — log lines, recordings, the status tool's "how long ago" — is
measured against it.

**Unfixed.** Installing `fake-hwclock` would make it monotonic-ish across
reboots, which is not correct but is far better than 1970.

---

## 8. `~/.ssh/config`'s `photonvision` alias points at a dead address

**Condition.** `ssh photonvision`.

**What happens.** It hangs, pointed at `192.168.1.202`, which the Pi has not
used since it moved to the robot network.

**Unfixed.** Use `ssh -i ~/.ssh/photonvision photon@10.54.59.12`.

---

# Found by these tools, fixed elsewhere

## The robot code asks for cameras that do not exist

Not a defect in this repo, recorded because these tools are what surfaced it
and the next person will meet the same symptom.

`PlankBot2026.jar`, branch `betterVisionIntegration`, `2b2c8e2`, built
2026-01-17, hardcodes lowercase camera names in three places:

```
frc/robot/subsystems/vision/VisionConstants     camera0Name="left"  camera1Name="right"
frc/robot/Constants$Vision                      "left", "right"
frc/robot/subsystems/oldVision/CameraConstants  "left", "right"
```

PhotonVision publishes `Left` and `Right`. NT paths are case-sensitive, so the
robot's vision has never worked — and it fails silently, because asking for a
camera that does not exist is not an error in photonlibpy. See §2 for what the
leftover tables look like from the outside.

The single-topic test: fix the names, deploy, and
`/photonvision/Left/pipelineIndexRequest` appears. That topic existing is proof
the robot is asking for a camera that is really there.
