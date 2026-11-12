# Session runbook

One evening, in order, with the decision points called out. Print it and tick
the boxes. QUICKSTART.md is the same ground with explanations; README.md is
why. This is the one to hold while the robot is on blocks.

Goal: **`robotToCamera` for every camera, measured not guessed.** Everything
else here exists to make that number trustworthy.

**Order matters and is not arbitrary:** calibration gives photontune the `fx`
it needs, the layout gives it multi-tag solves to score on, and the spin is
only as good as the detections underneath it. Doing these out of order does not
error — it produces confident wrong numbers.

Fill this in as you go. `RIO` below = the roboRIO's IP address.
Write it here once: `RIO = ______________________`

---

## 0. Laptops — do this first, all of them

A stale clone does not error. It produces **wrong numbers quietly**, which is
worse. Three fixes landed on GitHub this afternoon and two of them change
answers.

```sh
conda activate photon          # every new terminal, every time
cd photonvision-tools
git pull
pip install -r requirements.txt -r requirements-survey.txt
```

- [ ] pulled on every laptop
- [ ] `conda activate photon` understood — a new terminal needs it again

> **Anaconda rule:** once the environment exists, use **`pip` only** for these
> requirements. `conda install` on top of a `pip install` half-overwrites
> packages and the import errors look like corruption.

---

## 1. Robot network — the three that waste evenings

- [ ] **Pi's FRC address is a *secondary*.** Keep DHCP as the way back in.
      Lose that and you are on a monitor and keyboard.
- [ ] **photontune's unit points at the right roboRIO.** The shipped unit has
      `--nt-server 10.0.0.2` hardcoded.
- [ ] **`runNTServer` is FALSE.**

```sh
sudo python3 config/set_nt_server.py false
sudo systemctl restart photonvision
```

> Two NT servers on one network fight, and the symptom is **silence**, not an
> error. On the robot the roboRIO is the server — so **every tool's `--host` is
> the roboRIO, not the Pi.** Pointing `--host` at the Pi is the single most
> common way to lose an hour: the tools sit there seeing nothing and look broken.

---

## 2. Calibration — 60 seconds, then decide

```sh
python3 survey/photon_calib.py --host $RIO --running 1280 800
```

Change `1280 800` to whatever resolution you will **actually run**.

Read the output:

| what it says | what to do |
|---|---|
| both cameras listed, calibration at your run resolution, 50+ snapshots | **nothing — skip to step 3** |
| a camera says `NO CALIBRATION AT ALL` | calibrate it, below |
| `*** NOTHING AT 1280x800` | calibrate it, below |
| present but under 50 snapshots | works; redo it if there is time |

- [ ] every camera has a calibration at the run resolution

> A camera with no calibration **for its active resolution publishes nothing at
> all** and looks exactly like dead hardware. This is the one failure that
> wastes a whole session.
>
> Calibration travels with the **camera**, not the location — moving the Pi from
> home to the robot does not invalidate it. What does: the lens was refocused,
> the lock ring moved, the camera was swapped, the resolution changed, or it
> took a real knock.
>
> `robotToCamera` is the opposite. **Remounting invalidates it.** That is why
> step 5 is still on tonight's list even if calibration is fine.

### Only if you need to calibrate

```sh
python3 calib/calib_view.py --host $RIO            # mirrored, so left is left
python3 calib/charuco_capture.py --host $RIO --camera "NAME" --dry-run
python3 calib/charuco_capture.py --host $RIO --camera "NAME" \
    --width 8 --height 8 --square 1.0 --marker 0.75 --family 4x4 --end
```

**It runs itself and stops on its own.** Twelve phases, 106 shots — centre,
four corners, four edges, far back, close in, fill-in. It names each phase,
counts you down, and fires the shots. You just move the board. There is no key
to press to finish it.

`--end` is **not** a stop command. It is a decision made at launch about what
happens *after* the plan finishes on its own:

| you launched with | after the last shot |
|---|---|
| `--end` | computes and **STORES** the calibration, **replacing** the existing one |
| no `--end` | takes all the same shots, then writes nothing — the old calibration survives |

- Check the board numbers against the printed sheet before the real run.
- Watch **kept**, not shots. A snapshot where the board was not fully found is
  discarded silently, so "I took 120" routinely means "it kept 80".
- `--dry-run` walks the whole plan and takes nothing — the right rehearsal.
- Ctrl-C at any point abandons it. Nothing is written, old calibration intact.
- Repeat per camera.

---

## 3. Field survey — the phone

**Build the 1/3 field completely first. Then survey. Then do not move a tag.**
The layout is only true for tonight's arrangement.

On the phone (PhotonField):

- [ ] **Capture tab.** Press Record. It says `Hold still — 4s` with a countdown.
      Stand still until it clears. *(ARKit's map is built from almost nothing at
      session start. Walking into that cost the worst survey 96 mm in its first
      few seconds.)*
- [ ] Walk the tags. Each tag grows a ring: the **percentage is progress**, the
      **dark part of the ring is where to walk next**. Aim to see each tag from
      both sides, not just head-on.
- [ ] Keep two tags in one frame wherever you can. A tag sharing 50+ frames with
      two others is pinned by geometry and stops caring about tracker drift
      entirely — measured, it is the difference between 5 mm and 23 mm.
- [ ] When **"All tags done — STOP"** appears, press **Finish — solve it**.
      More walking past this point buys nothing.

On the **Solve** tab:

- [ ] `tag size m` = `0.1651` (6.5 in) unless your tags differ
- [ ] `length m` / `width m` = your field extents
- [ ] **`ref tag height m`** = height from the floor to the origin tag's centre
- [ ] **Pin an origin tag** in the picker — do not leave it on "best observed",
      or a re-survey next practice hands the origin to a different tag and moves
      every path you have drawn
- [ ] Press **Solve** *after* typing the height. Export is blocked until you do.
- [ ] Read the summary: **drift**, **gravity**, **scale check**, and any
      `FLAT: tag N` line

| summary line | meaning |
|---|---|
| drift under ~50 mm | good |
| drift over ~70 mm | **re-survey now, while the tags are still up** |
| `FLAT: tag N face up or down` | that tag is lying on the floor. Remove it from the room and re-survey, or delete it from the file. It is not a field tag and a pose estimator will use it. |
| `levelled to gravity: z was N deg off vertical` | expected, usually under 3° |
| LiDAR ratio far from 1.00 | your typed tag size disagrees with the depth sensor |

- [ ] **Export field.json**

---

## 4. Load the layout

PhotonVision → **Settings → Import Field**. No terminal.

- [ ] layout imported
- [ ] cameras are producing **multi-tag** solves (step 5 needs them)

---

## 5. photontune — exposure, in the light you will play in

> **This has to come before the spin, and it is a hard dependency rather than a
> preference.** photontune reads the camera's OWN intrinsics to get `fx` for
> the blur budget, and refuses with "no calibration for the active resolution"
> without them — that is step 2. It scores each trial on the **multi-tag solve
> rate**, which needs the layout loaded — step 4. And the spin in step 6 is
> only as good as the detections underneath it, so tuning exposure after
> measuring the mount means measuring the mount through the wrong exposure.


A dim room that **flickers** is the awkward case, and school classrooms and
old gyms are full of them. Observed 2026-10-29 in a school classroom. It
changes which command you run.

### Check for flicker FIRST — 30 seconds

```sh
python3 calib/calib_view.py --host $RIO
```

Watch the image for a few seconds. **Whole-frame brightness pulsing** — not
banding, the entire picture getting lighter and darker — is mains flicker.
Global-shutter sensors show it that way.

> **photontune has NO flicker detection.** A flickering room reports as a dark
> room, and nothing tells you the measurement was being modulated underneath.
> This check is the only thing standing between you and a confidently wrong
> exposure.

### Why it happens, and why exposure cannot fix it

60 Hz mains pulses light at **120 Hz — an 8.33 ms period**. The default 863 µs
exposure samples **10% of that**, at a different phase every frame, so
brightness swings frame to frame.

Averaging the flicker out needs a whole period, 8.33 ms, which is **58 px of
smear** at 360 °/s against a 6 px budget. In that room, flicker-immune and
blur-immune are mutually exclusive. You are choosing, not fixing.

### What to run

| situation | command |
|---|---|
| normal light, no flicker | `python3 /opt/photontune/photontune.py --host 127.0.0.1` |
| **dim and/or flickering** | `python3 /opt/photontune/photontune.py --host 127.0.0.1 --blur-rate 90 --dwell 12` |

- [ ] flicker checked in calib_view
- [ ] tune run with the right flags

`--blur-rate 90` gives **4× the exposure** (863 → 3454 µs). It is the honest
lever: 360 °/s is the harshest case, but a pose fix taken mid-spin is discarded
downstream anyway — the shots that matter happen while lining up, nearer
90 °/s. `--dwell 12` tightens the error bar 1.7× and triples the run time.

**Do not** try to phase-lock the frame rate to the flicker. The sensor runs off
a crystal and the grid does not, so the phase walks through a full cycle in
minutes — and it would trade a wide error bar for trials that are individually
quiet and not comparable to each other.

### Triggering it from NetworkTables

photontune publishes under **`/PhotonTune`**. Set `run` true to start a tune.

| topic | |
|---|---|
| `run` | **set true to start**; clears itself when finished |
| `busy` | true while tuning |
| `progress` | 0 to 1 across all cameras |
| `ok` | **go / no-go for the last run** |
| `summary` | `OV9281=863@g40`, or `FAILED: ...` |
| `status` | live line |
| `warnings` | not failures, but read them |
| `heartbeat` | ticks constantly — **frozen means the service is dead** |
| `camera` | which camera, "2 of 3" |
| `result` | full JSON |

**Elastic triggers it. AdvantageScope cannot.**

- **Elastic:** drag `/PhotonTune/run` onto the layout, change the widget type to
  **Toggle Switch**. Add `ok`, `summary`, `status`, `progress`, `heartbeat` as
  Text Displays. Shuffleboard and Glass also write.
- **AdvantageScope** displays every topic fine but **cannot flip `run`** —
  Tuning Mode only edits fields under `/Tuning`, and `/PhotonTune/run` is not
  there. Watch in AdvantageScope, trigger in Elastic.

- [ ] **check `heartbeat` before pressing anything.** If the daemon has died,
      `ok` and `busy` hold their last values forever and the button does nothing

---

## 6. robotToCamera — two motions, one solve

**Two things move the robot. The third command does not touch it.**

### 5a. Spin

```sh
python3 mount/calibrate_mount.py record spin.npz 60 \
    --host $RIO --motion spin --gyro-topic /SmartDashboard/Gyro
```

Counts down `SPIN IN PLACE in 3… 2… 1…` then records for 60 s.

**Spin in place, two full turns, slowly, tags in view the whole time.**

### 5b. Straight drive

```sh
python3 mount/calibrate_mount.py record drive.npz 30 \
    --host $RIO --motion drive --gyro-topic /SmartDashboard/Gyro
```

Counts down `DRIVE STRAIGHT in 3… 2… 1…` then records for 30 s.

**Drive 2–3 m dead straight, no steering input, tags in view.**

> Not a second spin. A spin recorded as the drive does not fail cleanly — it
> defines "robot forward" as whichever way the robot drifted.

### 5c. Solve — robot stays parked

```sh
python3 mount/calibrate_mount.py solve spin.npz --forward drive.npz \
    --origin-height 0
```

`--origin-height 0` is right **because you put the floor height into the phone**
in step 3, so the layout's z = 0 is already the floor.

Re-solving is free. Got an option wrong? Run it again — no re-driving.

### Read it in this order

1. **FIT QUALITY** — prints *above* the transform. That residual is the error
   bar on x and y in millimetres.
2. **CROSS-CHECK** — the spin measured camera-to-camera yaw without ever seeing
   the drive; the drive gives each yaw separately. Two independent
   measurements, so agreement is real evidence. Expect under ~1°.
3. **TILT sd** — pitch and roll are constant through a spin. A large sd means
   the axis was not vertical.
4. **GYRO SCALE** — want `+1.0000`. A sign error or a scale error is the half of
   a gyro fault that **no pose reset ever absorbs**.
5. **robotToCamera** table, then the Java block.

### What it refuses to do

| condition | result |
|---|---|
| radial residual > 5 mm | **refuses** to print Java/JSON — the spin axis wandered |
| fitted radius > 1 m | **refuses** — that was an arc, not a spin in place |
| robot rotated > 3° during the drive | **refuses** — not a heading reference |
| travel < 500 mm | **refuses** — pose noise swamps the direction fit |
| cameras disagree > 2° on heading | warns — suspect one camera's calibration |

If it refuses, the **table** above still has correct yaw, pitch, roll and z —
none of those come from the circle fit. Only x and y are affected.

- [ ] Java block copied into robot code

---

## 7. Before you pack up

- [ ] **Press Solve on the phone while the tags are still up.** The drift number
      is the one judgement worth making in the room.
- [ ] `/PhotonTune/heartbeat` is ticking — frozen means the service is dead
- [ ] exposure summary recorded somewhere: `________________________`
- [ ] `runNTServer` still false
- [ ] `spin.npz`, `drive.npz`, `frames.json`, `field.json` copied off the laptop

---

## The four silent failures

None of these produce an error. All of them produce wrong answers.

1. **`--host` pointed at the Pi instead of the roboRIO.** Tools see nothing.
2. **Calibrated at a different resolution than you run.** Camera publishes
   nothing at all.
3. **Surveying before the field is finished**, or nudging a tag afterwards.
4. **A stray tag on the floor** getting solved into the layout. The app now
   flags it — read the `FLAT:` line.

---

## If you only get through step 2

That is still the right night's work. Calibration is the gate everything else
sits behind, and a measured `robotToCamera` on top of a bad calibration is a
precise wrong answer.
