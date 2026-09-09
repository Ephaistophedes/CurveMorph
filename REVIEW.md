# Review and replacement design

The original 2.1 add-on is preserved in `backups/mouth_rig_2_1_0.zip`.
Paths and line numbers below refer to that archived version, not version 3.

## Original findings

| Priority | Bug / limitation | Original location |
| --- | --- | --- |
| High | Preview creation adopts globally named placement objects; clearing deletes every object in a globally named collection, including unrelated objects. | `widgets.py:166`, `widgets.py:287` |
| High | Cleanup trusts stored object names and can delete the wrong rig after duplication, renaming, or name reuse. | `operators.py:71` |
| High | Rebuild deletes the previous rig before conflict checks and before the replacement is known to work; a failed build loses the existing rig and its weights. | `operators.py:425` |
| Medium | Mirroring only synchronizes the selected corner before rebuilding all widgets, so other manually moved controls can revert to stale stored positions. | `operators.py:528` |
| Medium | Preview size/offset changes delete and recreate all widgets, losing selection and creating avoidable datablock churn. | `widgets.py:245` |
| Product gap | Four fixed bone strands, no configurable control count, no local influence radius, and no shape-key export. Automatic weights add a manual repair step. | `rig_builder.py`, `operators.py`, `ui.py` |

Python compilation passed, but the original checkout had no tests and its `.git`
directory did not contain a usable repository. The high-priority findings came
from direct code inspection, not destructive reproduction on a user's file.

## Implemented plan

1. Replace armature generation and automatic weighting with a native cyclic
   Bézier curve containing 4–64 editable points.
2. Bind nearby vertices once using distances along connected mesh edges. Cache
   that numeric mapping; update only the temporary shape-key preview when the
   curve changes. A compact smooth falloff controls the influence boundary.
3. Apply the difference between posed and resting curve samples. Creating or
   resetting controls therefore preserves the original mesh, even when a small
   number of points only approximates the selected loop.
4. Save ordinary relative shape keys with explicit coordinates, excluding
   modifiers. Keep existing keys and weights intact. Store ownership using
   object pointers and a session token, never global object names.
5. Separate numerical geometry, Blender session lifecycle, operators, and UI.
   Archive the old implementation; remove unused rig and widget modules.
6. Add numerical and Blender integration coverage, including save/reload,
   transformed objects, radius changes, invalid input, cleanup, and undo/redo.
7. Version 3.1 adds persistent, revisitable loop/corner setup and an optional
   weighted vertex-group mask. Corner controls are pinned exactly to selected
   vertices. Mask editing preserves poses; applying changed loop/corner selections
   explicitly resets controls while retaining saved keys. Reconfiguration rolls
   back if creation of replacement controls fails.

## Known boundaries / next refinements

- Author on the neutral Basis, with other shape keys at zero and any existing
  character rig in its neutral pose. Existing modifiers are retained, but the
  controls are placed on the undeformed source mesh.
- This is a shape-authoring tool, not an animation dependency system. Finished
  keys are native Blender data and need no add-on to play back.
- Distance follows mesh edges, an approximation of continuous surface distance.
  Disconnected geometry is excluded; attached mouth interior within the radius
  can move. Contact/collision and volume preservation are not implemented.
- Control counts are chosen at setup or changed by a deliberate reset/rebuild.
  Manual subdivision/deletion of curve points is rejected to preserve binding.
- Potential follow-ups after artist feedback: symmetric
  editing controls, optional influence visualization, and pose presets.
