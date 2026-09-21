> **AI usage:** AI used · human checked · real-world validated.
> The source tracing and the test harness were AI-assisted; every claim below was
> verified by me against the v2026.3.4 tag and measured on physical hardware.

Thanks for writing the unit test — it's what made this tractable, because it
told us where the bug *isn't*.

**Your test passes because it exercises the CPU path. This rig is on the
libcamera GPU path, and only that path is affected.**

In `v2026.3.4`:

```java
// CpuImageProcessor.java:68 and :95 — the CPU path rotates BOTH
m_rImagePipe.run(input.colorImage.getMat());
... input.staticProps.rotate(m_rImagePipe.getParams().rotation())

// LibcameraGpuFrameProvider.java:106 — the GPU path rotates the CALIBRATION
settables.getFrameStaticProperties().rotate(settables.getRotation())

// LibcameraGpuSettables.java:223 — but only 180 ever reaches libcamera
LibCameraJNI.createCamera(
        getConfiguration().matchedCameraInfo.path(),
        mode.width,
        mode.height,
        (m_rotationMode == ImageRotationMode.DEG_180_CCW ? 180 : 0));
```

`LibCameraJNI` has no other rotation entry point (`grep -i rotat` on it returns
nothing else), and `requestFrameRotation()` only calls `settables.setRotation()`,
which stores the mode and re-runs `setVideoModeInternal`.

So for `DEG_90_CCW` and `DEG_270_CCW` on a CSI camera, `rotateCoefficients()`
swaps fx/fy, moves the principal point and exchanges p1/p2 — while the pixels
are never rotated. Detection then runs against a camera matrix that does not
describe the image it is looking at. Note the image size isn't swapped for the
camera either, only in `FrameStaticProperties`.

That also explains the title: **the published corners stay correct**, because
they are detected in the real, unrotated image. Only the pose solved from them
is wrong.

### How a user reaches this

It is one control on the first tab of the dashboard — a `pv-select` labelled
**"Orientation"** (`photon-client/src/components/dashboard/tabs/InputTab.vue:171-178`),
sitting alongside exposure and brightness. Mounting a camera sideways to fit a
robot is common, and this is where you would go to correct it.

What makes it hard to catch is that every visible signal says it worked.
`photon-camera-stream.vue:41-42` rotates the dashboard preview for modes 1 and 3,
so the picture turns the right way up:

- preview looks correct
- tags are detected
- overlays land on the tags
- reprojection error is normal (0.196 px here at `DEG_90_CCW`, against 0.199 at
  `DEG_0`)
- multi-tag solves succeed

and the pose is silently out by ~25 cm, with nothing anywhere indicating it.

A team that hits this has no reason to suspect the rotation setting. The likely
path is recalibrating the camera, re-surveying the field layout, or retuning
`addVisionMeasurement` standard deviations — all of which leave the fault in
place.

It is also worth noting which configuration is affected: CSI camera on a
Raspberry Pi, which is the common FRC setup. A USB camera on the same Pi goes
through `CpuImageProcessor` and should be unaffected, which may be why this has
persisted — the people most likely to investigate are on the path that works.

### Falsifiable prediction, and it held

If this is the mechanism, `DEG_180_CCW` should be *unaffected*, because 180 is
the one value that reaches libcamera. Measured on a stationary camera, A/B/A,
reading `multitagResult.bestTransform` over the websocket, ~160 samples per
mode:

| mode | field→camera translation | Δ from DEG_0 |
|---|---|---|
| `DEG_0` | `[+2.6736 −1.5948 −0.2979]` | — |
| `DEG_90_CCW` | `[+2.5095 −1.6895 −0.4681]` | **0.2547 m** |
| `DEG_180_CCW` | `[+2.6827 −1.5867 −0.2834]` | 0.0190 m |
| `DEG_270_CCW` | `[+2.4972 −1.7260 −0.4358]` | **0.2595 m** |
| `DEG_0` (repeat) | `[+2.6739 −1.5937 −0.2982]` | 0.0012 m |

The camera did not move, so every row should read zero. 180° is clean and
90°/270° are not — which is the signature of "only 180 is passed through".

The 0.0012 m A/B/A repeat shows the rig itself was stable across the sequence.

### Earlier measurement in this issue

The original 0.344 m figure compared PhotonVision's pose against an independent
`SOLVEPNP_SQPNP` on PhotonVision's own published corners. That comparison is
consistent with the above: the independent solve returned the *same* answer at
`DEG_0` and `DEG_90_CCW` (0.199 px and 0.196 px corner RMS respectively), while
PhotonVision's moved. A low corner RMS in both modes also rules out the obvious
alternative explanation — that the independent solve used unrotated intrinsics
against rotated corners — since a principal point displaced by ~200 px could not
fit corners to 0.2 px.

### Environment

- PhotonVision **v2026.3.4** (linuxarm64), Raspberry Pi 5
- Arducam OV9281 global shutter, **CSI**, 1280×800, calibrated at that resolution
- `doMultiTarget` enabled, AprilTagFieldLayout loaded, `decimate=2`, `threads=1`
- Principal point is off-centre (cx 616.4, cy 393.6) — worth noting, since a
  synthetic test with a perfectly centred principal point would be insensitive
  to part of this

### Where a fix might go

Either pass the real rotation through to `createCamera` (and swap width/height
for the camera as well as for `FrameStaticProperties`), or — if libcamera cannot
do 90/270 cheaply — apply `RotateImagePipe` on the GPU path as the CPU path
does, so the image and the calibration always agree. A third option is to reject
90/270 on CSI cameras at the settings layer rather than accept them and produce
a wrong pose silently.

Happy to run further tests on this hardware if that would help — including a USB
camera on the same Pi, which by this reading should be unaffected.
