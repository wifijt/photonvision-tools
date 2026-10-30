# Tonight's card

Short version, in order. No explanations — those are in README.md and SETUP.md.
Replace `PI` with the Pi's **IP address** (not `photonvision.local`; mDNS usually
fails on a team network — find it with `hostname -I` on the Pi).

---

## First: bench or robot? It changes three things

Everything below assumes a **bench** — a Pi with no roboRIO. If the Pi is
**mounted on the robot with the roboRIO powered and on the network**, read this
box first, because getting it backwards makes the survey and mount tools report
*no data* instead of an error. That looks like a camera or lighting fault for
twenty minutes.

| | bench, no roboRIO | on the robot, RIO powered |
|---|---|---|
| `runNTServer` | **true**, then restart PhotonVision | **false** — the RIO is the server; two fight |
| `--host` for `capture_corners` / `calibrate_mount` | the **Pi** | the **roboRIO**, `10.TE.AM.2` |
| photontune's unit | `--nt-server 127.0.0.1` | `--team <number>`, and **no** `--nt-server` |

### On the robot, do these three things

**1. Give the Pi its FRC address**, as a *secondary* so DHCP stays as your way
back in:

```sh
nmcli -t -f NAME con show --active          # get the profile name
sudo nmcli con mod <profile> +ipv4.addresses 10.TE.AM.12/8
sudo nmcli device reapply eth0              # does not drop your ssh session
ip -4 -brief addr show eth0                 # expect BOTH addresses
```

It survives PhotonVision's own network re-apply (that only touches
`ipv4.method`), and it is stored in the profile, so it returns every boot with
no DHCP needed. Only switch the dashboard to **Static** when the robot goes to
competition — that drops DHCP entirely, which is right there and a liability
anywhere else.

⚠️ **Do not add this address to a Pi living on a 192.168.x network.** Avahi will
advertise it, clients may pick it, and `photonvision.local` then points
somewhere your laptop cannot route. The IP keeps working; the *name* stops.

**2. Fix photontune's unit.** The shipped unit has `--nt-server 10.0.0.2`, and
**`--nt-server` takes precedence over `--team`** — so it would hunt for a server
at 10.0.0.2 and never find your RIO, and the dashboard trigger would silently do
nothing. `--team` also defaults to **0**, so removing one without adding the
other is equally broken. Edit `/etc/systemd/system/photontune.service`:

```
ExecStart=/usr/bin/python3 -u /opt/photontune/photontune.py \
    --daemon \
    --host 127.0.0.1 \
    --team <your number> \
    --nt-table PhotonTune \
    --boot-timeout 90
```

`--nt-server` gone, `--team` present. Then:

```sh
sudo systemctl daemon-reload && sudo systemctl restart photontune
sudo journalctl -u photontune -f
```

`--team` beats a hardcoded `10.TE.AM.2` because ntcore tries the whole standard
FRC address set — roboRIO USB, mDNS, and the static address — instead of one
guess.

**3. Leave `runNTServer` false**, and skip the `set_nt_server.py` step in part 3
below. Point those tools at the RIO instead.

---

## 0. Laptop setup, once

```sh
git clone https://github.com/wifijt/photonvision-tools
cd photonvision-tools
pip install -r requirements.txt -r requirements-survey.txt
```

No internet? See [SETUP.md](SETUP.md), section 0b.

---

## 1. photontune onto the Pi

Get the bundle (any machine with internet — Windows, Mac, Linux):

```sh
curl -LO https://github.com/wifijt/photontune/releases/latest/download/photontune-offline-full.tar.gz
tar -xzf photontune-offline-full.tar.gz
```

Copy it over and run one command. Login is `photon` / `vision`:

```sh
scp -r photontune-offline-full photon@PI:/tmp/
ssh photon@PI
sudo bash /tmp/photontune-offline-full/install.sh
```

Needs no network on the Pi and no pip there to begin with.

Check it, no camera needed:

```sh
python3 /opt/photontune/sabotage_test.py --verdict-matrix
```

Safe first real command — asserts settings, no exposure sweep:

```sh
python3 /opt/photontune/photontune.py --host 127.0.0.1 --baseline-only
```

**STOP if that fails.** Nothing below will work.

Then the real tune, pointed at tags, in the light you will play in:

```sh
python3 /opt/photontune/photontune.py --host 127.0.0.1
```

### If the room is dark, or the lights flicker

Observed 2026-10-29 in a school classroom: visible frame-to-frame flicker in
the camera viewer, and too dark to meet the blur budget.

Mains at 60 Hz makes light pulse at **120 Hz — an 8.33 ms period**. The default
863 µs exposure samples only **10%** of that, at a different phase every frame,
so brightness swings frame to frame. On a global-shutter sensor that shows as
the whole frame pulsing, not as banding.

**You cannot fix this with exposure.** Averaging the flicker out needs a full
period — 8.33 ms — which is **58 px of smear** at 360 °/s, nearly 10× the 6 px
budget. Flicker-immune and blur-immune are mutually exclusive in that room.

What actually helps, best first:

| lever | effect | cost |
|---|---|---|
| `--blur-rate 90` | **4× the exposure** (863 → 3454 µs) | smear during fast spins |
| `--dwell 12` | error bar **1.7× tighter** | run takes 3× longer |
| 640×400 | 2× exposure again (`fx` halves) | detection range; needs its own calibration |

```sh
python3 /opt/photontune/photontune.py --host 127.0.0.1 --blur-rate 90 --dwell 12
```

`--blur-rate` is the honest one. 360 °/s is the harshest case, but a pose fix
taken mid-spin is discarded downstream anyway — the measurements that matter
happen while lining up, at perhaps 90 °/s. `--dwell` helps because 4 s already
spans ~480 flicker cycles, so the *mean* is unbiased; it is the **error bar**
that defeats the gain walk, and that shrinks as √frames.

Do **not** try to phase-lock the frame rate to the flicker. The sensor runs off
a crystal and the grid does not, so the phase walks through a full cycle in
minutes — and it would make things worse, trading a wide error bar for trials
that are individually quiet and not comparable to each other.

⚠️ photontune has **no flicker detection**. A flickering room reports as a dark
room, and nothing tells you the measurement was being modulated underneath.

---

## 2. Calibrate each camera

⚠️ **Do this at the resolution you will actually run.** A camera with no
calibration for its active resolution publishes **nothing at all** — it looks
exactly like a dead camera.

On the laptop. Open the mirrored view first so left is left:

```sh
python3 calib/calib_view.py --host PI
```
→ open <http://localhost:8078>, pick the camera.

Rehearse (captures nothing, writes nothing):

```sh
python3 calib/charuco_capture.py --host PI --camera "NAME" --dry-run
```

For real — **check the board numbers against your printed sheet**:

```sh
python3 calib/charuco_capture.py --host PI --camera "NAME" \
    --width 8 --height 8 --square 1.0 --marker 0.75 --family 4x4 --end
```

- Watch the **kept** count, not the shot count.
- Nothing is written until `--end`. Ctrl-C costs you nothing.
- Repeat per camera.

---

## 3. New field layout

⚠️ **On a bench, turn PhotonVision's NT server ON first, or steps 3 and 4
silently see no data** — they are NetworkTables *clients* and there is no
roboRIO. **On the robot, skip this and use the roboRIO as `--host`** — see the
box at the top.

```sh
ssh photon@PI
sudo python3 /path/to/config/set_nt_server.py true
sudo systemctl restart photonvision          # required; config is read at boot
```

⚠️ **Measure a printed tag with a ruler.** `tag_size` is the only thing setting
the scale of your whole field. 1% wrong print = 1% wrong field, undetectable.

On the laptop:

```sh
python3 survey/photon_calib.py PI                    # sanity-check intrinsics
python3 survey/capture_corners.py --host PI frames.json     # move the camera around
python3 survey/solve_layout.py frames.json layout.json 0.1651   # <- YOUR measured size
```

`photon_calib.py` takes the host **either way** — `PI` or `--host PI`. It used
to be positional only, so `--host PI` tried to resolve a machine called
`--host` and died in `getaddrinfo` naming neither the flag nor the address.

**Hold the camera in your hands, and move-then-pause.** Hand-held beats a robot
mount here: a bundle adjustment wants viewpoint spread, and a robot is stuck at
one height and one pitch. Step to a new spot, hold still a beat, step again —
`capture_corners` de-duplicates on a 25 px signature, so holding still cannot
flood the file, and it has **no blur filter**: a smeared frame is kept and
weighted like a sharp one. Vary distance, angle and *height*, not just position
along the wall.

While capturing: every tag must be seen **together with at least two others**,
from **more than one** viewpoint. A tag seen on one link gets placed with false
confidence (DEFECTS #9). Read the `co-visibility:` line it prints at the end —
that, not the RMS, tells you whether the survey is any good.

**Minimum tags:** 2 will produce a layout; **3 with all pairs seen** is the
minimum worth trusting, because a triangle closes and a single link cannot be
checked.

Upload:

```sh
curl -F "data=@layout.json" http://PI:5800/api/settings/apriltagFieldLayout
```

### The reference tag is 0,0,0 — and it is a TAG, not the floor

`solve_layout` anchors on the **best-observed** tag (most observations, not the
lowest ID) and prints it: `reference 2 (310 observations)`. Every x/y/z is a
delta from that tag's centre, so **z is a height difference from a tag, not a
height above the carpet**.

Two consequences:

- Measure **that** tag's centre height above the floor, after the solve tells
  you which one it is. Re-running the survey can hand the anchor to a different
  tag — re-read the line, don't assume.
- Step 4 needs that number as `--origin-height`. X and Y don't need it; z does.

The `"field": {"length": 8.0, "width": 8.0}` in the output is a **placeholder**,
not a measurement. Nothing in this repo reads it; WPILib only uses it for
`setOrigin(kRedAllianceWallRightSide)`.

---

## 4. Robot camera offsets → `robotToCamera`

Two recordings. The layout from step 3 must already be uploaded — this reads
PhotonVision's **multi-tag** pose, which does not exist without it.

**4a. The spin.** Robot in place, slowly, two full turns, tags in view the
whole time.

```sh
python3 mount/calibrate_mount.py record spin.npz 60 --host RIO
```

**4b. One straight drive.** 2–3 m, no steering input, tags in view.

```sh
python3 mount/calibrate_mount.py record drive.npz 15 --host RIO
```

**4c. Solve both together.**

```sh
python3 mount/calibrate_mount.py solve spin.npz \
        --forward drive.npz \
        --origin-height 1.4351        # <- REF TAG height from step 3
```

Out comes a full `robotToCamera` per camera — x, y, z, roll, pitch, yaw — as a
table, as pasteable Java `Transform3d`, and as JSON.

### Why two recordings

The spin alone fixes **radius, z, pitch and roll** absolutely, and bearing and
yaw only as *differences* between cameras. What it pins without any anchor is
`K = bearing − mount yaw` per camera; what it cannot do is split that, because
every camera shares one unknown — **where robot forward points**. One number
for the whole rig, and the straight drive supplies it.

**"Forward" means the direction the robot actually travelled.** Not the
Pigeon's zero, not the chassis centreline. So if the steer offsets are off and
the robot crabs, that crab is *inside* these transforms. That is what you want
for vision — `robotToCamera` then lives in the frame the drivetrain really
moves in — and it is **not** a measurement of the crab.

### What it refuses to do

| condition | what happens |
|---|---|
| robot rotated > 3° during the drive | **refuses** — that is not a heading reference |
| travel < 500 mm | **refuses** — pose noise swamps the direction fit |
| cameras disagree > 2° on heading | warns — suspect one camera's calibration |
| spin and drive disagree on yaw-apart | flags `<<< DISAGREES` |

That last one is the real check: the spin measures camera-to-camera yaw without
ever seeing the drive, and the drive gives each yaw separately. Independent
measurements, so agreement is evidence rather than self-fulfilling. Expect
under ~1°.

### Read the sd columns

`pitch` and `roll` are **constant** through a spin, so their sd is a free check
on the spin itself — above 1° means the rotation axis was not vertical (lifted
wheel, flexing frame). Tilt is measured in the *field* frame, which equals the
robot frame only while the robot sits level.

Sanity-check the reported radius against a tape from the pivot to the lens, and
expect a few mm of entrance-pupil offset *inside* the barrel — the optical
centre sits behind the glass.

---

## 5. Before the Pi meets a roboRIO

```sh
sudo python3 config/set_nt_server.py false
sudo systemctl restart photonvision
```

Two NT servers on one network fight each other.

---

# Watching and triggering photontune from a dashboard

photontune publishes under **`/PhotonTune`**. Set `run` true to start a tune.

| topic | |
|---|---|
| `run` | **set true to start**; clears itself when finished |
| `busy` | true while tuning |
| `progress` | 0→1 across all cameras |
| `ok` | **go / no-go for the last run** |
| `summary` | `OV9281=863@g40`, or `FAILED: ...` |
| `status` | live line |
| `warnings` | not failures, but read them |
| `heartbeat` | ticks constantly — **frozen means the service is dead** |
| `camera` | which camera, "2 of 3" |
| `result` | full JSON |

## Elastic — use this one to TRIGGER it

1. Connect Elastic to the robot (team number, or the Pi's IP as the NT server).
2. Drag `/PhotonTune/run` onto the layout → change the widget type to
   **Toggle Switch**. That one is writable; flipping it sets the topic true.
3. Add `ok`, `summary`, `status`, `progress` and `heartbeat` as Text Displays.

## AdvantageScope — displays it, but CANNOT trigger it

AdvantageScope shows every `/PhotonTune` topic fine. Add them from the sidebar;
`progress` and `heartbeat` suit a line graph, the rest a table.

**It cannot flip `run`.** Editing requires Tuning Mode, and Tuning Mode only
edits fields published under the **`/Tuning`** table using the
**"NetworkTables 4 (AdvantageKit)"** live source. `/PhotonTune/run` is not under
`/Tuning`, so it is read-only there.

So: **watch in AdvantageScope, trigger in Elastic** — or in Shuffleboard or
Glass, which also write.

(If you want AdvantageScope to trigger it, photontune would have to publish a
mirror of `run` under `/Tuning/`. Not done; ask and it is a small change.)

**Check `heartbeat` before you press anything.** If the daemon has died, `ok`
and `busy` hold their last values forever and the button does nothing.
