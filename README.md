# CurveMorph — Bézier Shape Keys

Select a closed mouth edge loop, pose it with native Bézier controls, and save
expressions as ordinary Blender shape keys. No armature or automatic weighting.

## Install

Download the latest `curvemorph_*.zip` from the
[Releases page](https://github.com/Ephaistophedes/CurveMorph/releases), or build
it yourself with `python tests/package_addon.py`. In Blender Preferences >
Add-ons, use **Install from Disk** and select that zip. Enable
**CurveMorph — Bézier Shape Keys**. Open the 3D View sidebar (`N`) and select
**CurveMorph**. Blender 5.0+ is declared; runtime tests were run on Blender 5.2.1 LTS.

## Pose a mouth

1. Select the character mesh. Use its neutral **Basis** and set other shape-key
   values to zero. If a character rig already exists, use its neutral pose.
2. In mesh Edit Mode, select **one connected, closed edge loop** around the
   mouth. Four or more vertices are required; select no extra vertices or edges.

   ![A closed 64-vertex mouth loop selected in Edit Mode, ready for Store Loop](docs/images/workflow_loop.png)

3. Click **Store Loop**. Click **Select / Refine Corners**, select exactly two
   vertices on that loop (one at each mouth corner), then **Store Corners**.
   Each corner gets an exact Bézier control; other controls follow the two lip arcs.
   Or click **Auto** to select the stored loop's minimum-X and maximum-X vertices,
   then **Store Corners** to confirm. Auto uses the neutral mesh's local X axis,
   regardless of the object's position, rotation, or current mouth pose.

   ![The cyan stored-loop guide during corner picking, zoomed on one mouth corner](docs/images/workflow_corners.png)

4. Optionally enable **Limit to Vertex Group**. Choose an existing group, or
   select affected vertices in Edit Mode and **Create Group from Selection**.
   Leave the option off to use distance falloff alone.
5. Choose **Control Points** (4–64; default 8), **Influence Distance**, and
   **Falloff**. Before creation, distance 0 chooses a mouth-sized starting value.
6. Click **Create Controls**. In the resulting curve Edit Mode, click a point
   and press `G` to move it. The mesh previews the change live.

   ![The finished 8-point Bézier ring around the lips after Create Controls](docs/images/workflow_controls.png)
   ![Close-up of a corner control point, pinned exactly to the stored corner vertex](docs/images/workflow_corners_detail.png)

7. Adjust **Influence Distance** and **Falloff** while posing. Distance measures
   travel along connected mesh edges in world units. During a session, 0 moves
   only the selected loop. Higher falloff concentrates the movement near it.
8. Enter a name and click **Save Shape Key**. The new key starts at value 0.
   **Reset Controls After Saving** starts the next expression from neutral.
9. Saving ends in Object Mode for reliable Undo. Click **Edit Controls** (or
   `Tab` with the control curve selected) to pose the next expression.
10. **Finish Session** removes the controls and temporary preview, keeping all
   saved keys. Select the character and use its normal Shape Keys sliders.

Automatic Bézier handles are the default. Select a point and use `V` >
**Aligned** or **Free** to edit its tangent handles manually. Move points and
handles freely in 3D. **Reset Pose** restores the initial points and auto handles,
and clears tilt.

Changing the count uses **Rebuild Controls**, which resets the current preview;
save any pose you want to keep first. Do not subdivide or delete curve points
directly during a session.

### Tilt / roll the lips

Select one or more curve control points in Edit Mode and use **Ctrl+T** to tilt
them; **Alt+T** clears the selected points' tilt. These are Blender's native
[Tilt and Clear Tilt tools](https://docs.blender.org/manual/en/latest/modeling/curves/editing/control_points.html#tilt).
Nearby mesh vertices now roll around the mouth-loop tangent, with live preview.
The stored loop is the twist axis, so pure tilt does not move its vertices.

Tilt uses the same **Influence Distance**, **Falloff Type**, **Falloff Strength**,
and overall vertex-group mask as movement. The optional **Twist Mask** group in
Setup adds a separate weight multiplier to twist only: weight 0 prevents twisting,
weight 1 allows full twisting, and fractional weights soften it. Moving the curve
points is unaffected by this extra mask. Leave the field blank to disable it.
The overall mask still applies to both movement and twist; the two masks multiply.
Saved shape keys bake both masks, so later group edits do not alter saved poses.

Use a nonzero influence distance with surrounding vertices
included in the mask; distance 0 leaves nothing off the axis to rotate. Start
with modest angles. Large twists can fold or compress the surface; this is not
a collision or volume-preservation solver.

Tilt is interpolated around the closed curve using its native tilt-interpolation
setting (Linear, Ease, Cardinal, or B-spline). **Save Shape Key** includes the
roll, and **Reset Pose** / **Rebuild Controls** return tilt to zero. Existing
sessions support tilt without rebuilding; previously saved keys remain unchanged.

### Influence falloff profiles

Choose **Falloff Type** before creating controls or change it live while posing:
**Smooth**, **Sphere**, **Root**, **Inverse Square**, **Sharp**, **Linear**,
**Constant**, or **Random**. These use Blender-style proportional-editing profiles,
but distance still follows connected mesh edges from the stored mouth loop.
The chosen profile applies to both movement and twist.

**Smooth** remains the default and preserves the previous behavior. **Falloff
Strength** is the existing exponent control: 1 uses the chosen profile as-is;
higher values concentrate influence. **Constant** uses full influence inside
the distance with a hard cutoff (strength has no effect). **Random** exposes a
**Random Seed** and keeps its per-vertex pattern stable while posing, changing
the radius, and reopening the file. Change the seed for a different pattern.
Constant and Random can create abrupt or irregular deformation; use them deliberately.

The selected loop retains full movement influence (subject to the overall mask).
Distance 0 affects only that loop; off-axis twist then has no effect. Disconnected
geometry remains excluded for every profile.

### Symmetrize a pose

Use **+X → −X** to keep the positive-X side and copy it onto negative X, or
**−X → +X** for the reverse. The plane is **X=0 in the character mesh's local
coordinates**, not the viewport or world axes. Controls are paired using their
neutral layout, even if posed points have crossed the centerline. Center controls
snap to the plane; tangent handles mirror with their left/right order reversed.
Tilt is copied to the mirrored side too.

Symmetrizing freezes handles to **Free** so the source pose and copied tangents
stay exact. Use `V` > **Automatic** to restore automatic handles if desired.
The action ends in Object Mode for reliable Undo; click **Edit Controls** to
continue. Saved keys, the Basis, and mask weights are unchanged. An asymmetric
mask or underlying mesh can still produce an asymmetric mesh result: this action
symmetrizes the control curve, not the character's topology or vertex weights.

## Refine setup at any time

The **Setup** section stays available on the character or its control curve.
Stored loop and corner selections survive finishing a session and saving/reopening
the `.blend` file. Use **Select / Refine Loop** or **Select / Refine Corners** to
recall a selection, adjust it, and store it again.

When choosing corners, the stored mouth loop is highlighted with a **cyan guide**
in the viewport. Its vertices remain independently selectable: only your chosen
corners turn orange. The guide disappears when you store corners, leave Edit
Mode, or switch to another setup step. Keep Viewport Overlays enabled to see it.
It creates no helper objects and does not affect renders, shape keys, or weights.

Entering setup editing pauses the preview on the neutral mesh without losing
your control pose. **Resume Current Controls** returns to that pose. Storing a
different loop or corner pair creates a pending setup: **Apply Setup** rebuilds
neutral controls, so save an unsaved expression first. Saved shape keys are kept.
**Discard Loop/Corner Changes** restores the last applied selections.

For the optional group, use **Select Affected**, **Assign**, **Remove**, or
**Weight Paint** to refine the mask. Group weights multiply distance falloff:
weight 0 prevents movement, 1 allows the full falloff, and fractional weights
soften it. This also applies to vertices on the mouth loop itself. Mask edits
preserve the control pose; resume after editing to see the result. Disabling the
mask removes the overall restriction; the separate Twist Mask still applies if
chosen. Saved keys bake the mask once, so later
group edits do not alter those expressions. Creating a group never overwrites
an existing group with the same name.

## What is preserved

- Creating/resetting controls does not smooth or reshape the original mesh.
  The curve supplies a displacement from its resting shape.
- Existing shape keys, vertex groups, modifiers, and armatures are retained.
  Saved keys contain only the mouth displacement, before modifiers.
- Disconnected teeth/eyes/mesh islands are not influenced. Connected mouth
  interior can move if it is inside the influence distance.
- Renaming the mesh or curve is supported. Each session owns its own curve and
  temporary key; cleanup never deletes a globally named collection.
- Sessions persist in `.blend` files. Re-enable the add-on to continue editing;
  completed shape keys need no add-on.

## Limits and recovery

Use local, single-user meshes and relative shape keys. Finish the session before
editing the source mesh topology or Basis. Set other expression values to zero
while authoring; save/export is blocked if they become active. Avoid changing
the temporary `__CurveMorph_...` key by hand.

If a control is deleted or the mesh changes, the panel reports the issue. Undo
the change and click **Refresh Preview**, or finish and create controls again.
This tool does not solve lip collisions or preserve volume automatically.

The Bézier curve approximates the selected loop; a low control count may leave
it slightly off the surface. The original loop detail is retained in the mesh.
Distance is measured along edges, so very irregular topology can produce uneven
falloff. The tool is for creating static expressions, not driving a rig at render time.

## Development

- `geometry.py`: loop validation, curve sampling, cached surface binding.
- `session.py`: owned Blender data, live preview, reset/save/cleanup/persistence.
- `setup.py`: persistent loop/corner selections and optional vertex-group mask.
- `corner_overlay.py`: non-selectable stored-loop guide while choosing corners.
- `operators.py`, `ui.py`, `__init__.py`: workflow and registration.
- `backups/mouth_rig_2_1_0.zip`: original implementation.
- `REVIEW.md`: original bug review and replacement plan.

Run `python -m unittest discover -s tests -p 'test_geometry*.py'` for numerical tests.
Run Blender with `--background --factory-startup --python-exit-code 1 --python`
followed by `tests/test_blender_integration.py`, `tests/test_blender_setup.py`,
`tests/test_blender_symmetry.py`, `tests/test_blender_corner_overlay.py`,
`tests/test_blender_auto_corners.py`, `tests/test_blender_tilt.py`, `tests/test_addon_registration.py`,
`tests/test_blender_twist_mask_falloff.py`,
or `tests/test_blender_undo.py`.
`python tests/package_addon.py` builds the installable ZIP.

For native handles, see Blender's [Bézier curve documentation](https://docs.blender.org/manual/en/latest/modeling/curves/structure.html).

## License

[GPL-3.0-or-later](LICENSE).
