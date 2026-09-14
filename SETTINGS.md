# PhotonVision AprilTag settings, in plain English

What each setting actually does, what it costs you, and which ones depend on where you are.

The important split:

- **Structural settings** are the same everywhere. Set once, verify with
  `config/apply_baseline.py`.
- **Lighting settings** change at every venue. That is what `photontune` is for.

---

## Structural — set once, same everywhere

### decimate

**How much the detector shrinks the picture while *looking* for tags.**

It searches a smaller image to save time, then measures the corners it finds at full
resolution. So it does not make your poses less accurate — it makes you blind to tags
that are far away, because a distant tag shrinks below what the search can recognise.

| decimate | search work | rough max range (6.5in tag, 1280x800) |
|---|---|---|
| 1 | full | ~18 m |
| 2 | 1/4 | ~9 m |
| 3 | 1/9 | ~6 m |
| 4 | 1/16 | ~4.6 m |

Measured: reprojection was 0.11–0.14 px at all four — genuinely no accuracy cost.
Framerate went 34 → 116 fps.

**Use 2.** It keeps ~9 m of range for tags you care about at 5–6 m. Do not raise it to
buy framerate; you are spending range you will need on a field, and a small test space
cannot show you the cost.

### numIterations

**How hard the detector works to polish each pose.** Default is 40. One rig was found at
222, which burned CPU for no measurable gain. Leave it at 40.

### threads

**How many CPU cores the detector uses.** Match your physical cores — 4 on a Pi 5.
Setting 8 does not go faster; it just makes timing less predictable.

### decisionMargin

**How confident the detector must be before it believes a tag is real.**

Higher rejects more marginal detections. Sounds safer, but too high throws away tags you
want: at 50 one rig was silently missing a tag entirely, and lowering to 35 recovered it.
False positives are handled better by multi-tag anyway.

**Use 35.**

### doMultiTarget (multi-tag)

**Solve one pose from all visible tags together, instead of each tag separately.**

This is the big one. A single tag viewed face-on is genuinely ambiguous — the maths has
two nearly equal answers and it flips between them. On one rig, 96% of single-tag
detections were above the standard reject threshold, and one tag confidently reported
itself 0.33 m away when it was really 2.4 m.

Using several tags at once removes the ambiguity entirely. **Turn it on.** It needs a
loaded field layout to know where the tags are.

### solvePNPEnabled

**Whether to output 3D poses at all.**

One trap: if there is no calibration for the resolution you are running, this does not
fall back to 2D — it throws an error every frame and outputs *nothing*. Turn it off
before switching to an uncalibrated resolution, then back on after calibrating.

### inputImageRotationMode

**Rotates the picture. Leave it at DEG_0.**

On v2026.3.4, any non-zero rotation corrupts the multi-tag pose by roughly 0.4 m — while
the corner data it publishes stays correct, so nothing looks wrong. The pose is stable,
repeatable, and in the wrong place. See PhotonVision issue #2613.

If your camera is mounted sideways, live with a sideways preview.

---

## Lighting — changes at every venue

### cameraExposureRaw

**How long the shutter stays open.** The single most important setting, and the one most
likely to be set wrong.

Longer means a brighter picture. It also means **motion blur**, which is what actually
breaks tag tracking on a moving robot. Blur scales directly with exposure:

```
blur = rotation speed x exposure x focal length
```

Measured on a real rig, same tag, same motion:

```
4735 us  ->  99% of frames detected
15000 us ->  76% of frames detected   (lost a quarter of them)
```

**Both looked perfect standing still.** That is the trap: a stationary test cannot see
the cost of a long exposure, so it will always tempt you longer than is safe.

Rough guide at 1280x800 — smear in pixels, against a tag about 70 px across:

| rotation | 2000 us | 4000 us | 8000 us | 15000 us |
|---|---|---|---|---|
| 90 deg/s | 3.5 | 6.9 | 13.9 | 26.1 |
| 360 deg/s | 13.9 | 27.8 | 55.6 | 104.2 |

If your robot spins hard while tracking, you want **short** — 2000 or below — and more
gain to compensate.

**If a tuner hands you a long exposure, that is a lighting problem, not an answer.**

### cameraGain

**Brightness amplification.** Costs image noise, but — crucially — **no blur**.

So the right instinct is always: exposure as short as your robot's movement demands,
then gain to recover the brightness, and accept a noisier picture.

### cameraBrightness

An offset added to the image. Low values crush the picture to black. If everything looks
black in a lit room, check this before anything else.

### White balance / red gain / blue gain

**Do nothing on a monochrome camera** (OV9281 and similar). There are no colour channels
to balance. libcamera will log errors about it; they are harmless.

---

## Not a setting, but decides everything

### Calibration

The camera's lens model. Get this wrong and every pose is wrong, no matter what else you
do — and targeting is affected too, since aim comes straight from the camera model.

One rig's principal point was 28 px off centre: a **2.84 degree aim error** on every shot,
present all season.

- **100+ images.** Upstream raised the minimum to exactly this.
- **Push the board into all four corners of the frame**, not just the middle. Lens
  distortion lives at the edges; if you never photograph the edges the solver invents it.
- **Tilt 30-45 degrees.** Flat-on views leave focal length poorly determined.
- **Keep the board flat.** A curled sheet bends the very distortion you are measuring.
- **Focus first, then calibrate, then never touch focus.** Calibration is only valid for
  the focus it was shot at.

### Field layout

Where the tags are. Multi-tag cannot work without it.

At an event, load the official one. **A layout from your practice space will make the
robot confidently report its position in your workshop's coordinates.**

---

## Which tool sets what

| | sets |
|---|---|
| `photontune` | exposure only (gain held fixed, or set with `--gain`) |
| `config/apply_baseline.py` | the structural settings above |
| `config/set_pipeline_setting.py` | anything, manually |
| `survey/` | builds a field layout for a custom tag setup |

`photontune` deliberately leaves the structural settings alone: they should not change
between venues, and a tuner that rewrote them could quietly undo a deliberate choice.
