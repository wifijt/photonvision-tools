
## Open: weakly-constrained tags (parked 2026-09-19)

`solve_layout.py` places a tag with one co-visibility link with the same
confidence as a tag seen with five, and says nothing about the difference.

Observed on tag 3 of the bench layout: it is only ever visible together with
tag 2, so its position is determined through a single pair. A bundle adjustment
cannot validate a constraint it has only once, and the solver emits a number
anyway. Downstream this showed up as the two-camera mount transform bending
0.77 deg and 24 mm between rig positions - tag 3's uncertainty leaking into a
figure that is supposed to be rigid.

The tag cannot be moved: the bench mirrors an FRC field, where tag positions
are fixed by FIRST. So the fix belongs in the tool, not the wall:

- report per-tag co-visibility degree and positional uncertainty
- flag single-link tags explicitly, naming what they depend on
- optionally take the official AprilTagFieldLayout position for tags the survey
  cannot determine, and solve only the rest
- have calibrate_mount.py weight or exclude weakly-placed tags

Not "tag 3 is misplaced" - tag 3 is UNKNOWABLE from that vantage point, and the
tool reported a confident number regardless.
