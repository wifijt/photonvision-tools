# Facts for #2613 — NOTES TO WRITE FROM, NOT TEXT TO PASTE

PhotonVision's policy: "Any and all prose should be human written, with no
generative AI usage. This includes issues and PR descriptions on GitHub."

So this file is deliberately not prose. It is the raw material. Write the
comment yourself, in your own words, as short as you like.

## The finding, in one line

Their unit test passes because it uses the CPU path; the bug is in the
libcamera path, where only 180 is ever passed to the camera.

## Source facts (v2026.3.4, verified against the tag)

- `LibcameraGpuSettables.java:223`
  `createCamera(path, mode.width, mode.height,
   (m_rotationMode == ImageRotationMode.DEG_180_CCW ? 180 : 0))`
  Only 180 reaches libcamera. Width/height not swapped for the camera.
- `LibcameraGpuFrameProvider.java:106`
  `settables.getFrameStaticProperties().rotate(settables.getRotation())`
  The calibration IS rotated, for every mode.
- `CpuImageProcessor.java:68` and `:95` — CPU path rotates the image AND the
  properties. Both.
- `LibCameraJNI` has no other rotation entry point.
- So at 90/270 on CSI: intrinsics rotated (fx/fy swapped, principal point
  moved, p1/p2 exchanged), pixels not rotated.

## Measured, my rig, camera stationary, A/B/A, ~160 samples per mode

Reading `multitagResult.bestTransform` over the websocket.

    DEG_0         [+2.6736 -1.5948 -0.2979]   baseline
    DEG_90_CCW    [+2.5095 -1.6895 -0.4681]   0.2547 m off
    DEG_180_CCW   [+2.6827 -1.5867 -0.2834]   0.0190 m off
    DEG_270_CCW   [+2.4972 -1.7260 -0.4358]   0.2595 m off
    DEG_0 again   [+2.6739 -1.5937 -0.2982]   0.0012 m off

180 is clean, 90 and 270 are not. That is the signature.
The 0.0012 m repeat shows the rig did not drift during the sequence.

## Why the earlier 0.344 m number was not a methodology error

Independent SQPNP on PhotonVision's published corners gave the SAME answer at
DEG_0 and DEG_90_CCW. Corner RMS 0.199 px and 0.196 px respectively. A
principal point displaced ~200 px could not fit corners to 0.2 px.

## User-facing path

- Dropdown labelled "Orientation", Input tab (`InputTab.vue:171-178`)
- `photon-camera-stream.vue:41-42` rotates the browser preview for modes 1/3,
  so the preview looks right
- Tags detected, overlays correct, reprojection normal, multi-tag solves fine
- Pose silently ~25 cm out

## Environment

PhotonVision v2026.3.4 linuxarm64, Raspberry Pi 5, Arducam OV9281 CSI,
1280x800 calibrated at that resolution, decimate 2, threads 1, multi-tag on,
field layout loaded. Principal point off-centre (cx 616.4, cy 393.6).

## Possible fixes (their call, not a suggestion to lead with)

Pass the real rotation to `createCamera` and swap width/height for the camera
too; or run `RotateImagePipe` on the GPU path as the CPU path does; or reject
90/270 on CSI at the settings layer rather than produce a wrong pose.

## Untested

USB camera on the same Pi — should be unaffected by this reading. I have not
tried it.
