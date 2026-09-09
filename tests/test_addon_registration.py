"""Use Blender's real restricted registration context, not only register()."""
from pathlib import Path
import sys
import addon_utils
import bpy

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root.parent))
for _ in range(2):
    addon = addon_utils.enable(root.name, default_set=False)
    assert addon is not None and hasattr(bpy.types.Object, 'curvemorph')
    addon_utils.disable(root.name, default_set=False)
    assert not hasattr(bpy.types.Object, 'curvemorph')
print('Restricted enable/disable twice: passed')
