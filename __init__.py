"""CurveMorph: a temporary Bézier deformer for authoring ordinary shape keys."""

bl_info = {
    "name": "CurveMorph — Bézier Shape Keys",
    "author": "CurveMorph contributors",
    "version": (3, 4, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > CurveMorph",
    "description": "Pose a selected mouth loop with Bézier controls and save shape keys",
    "category": "Mesh",
}

import bpy
from . import session, setup, operators, ui, corner_overlay

FALLOFF_ITEMS = [
    ('SMOOTH', 'Smooth', 'Smooth transition, matching the original mouth falloff'),
    ('SPHERE', 'Sphere', 'Rounded spherical influence profile'),
    ('ROOT', 'Root', 'Broad square-root influence'),
    ('INVERSE_SQUARE', 'Inverse Square', 'Broad inverse-square profile'),
    ('SHARP', 'Sharp', 'Concentrate influence near the mouth loop'),
    ('LINEAR', 'Linear', 'Straight-line decrease with distance'),
    ('CONSTANT', 'Constant', 'Full influence inside the distance, with a hard boundary'),
    ('RANDOM', 'Random', 'Stable per-vertex random influence, fading with distance'),
]


class CurveMorphSetup(bpy.types.PropertyGroup):
    falloff_type: bpy.props.EnumProperty(name='Falloff Type', items=FALLOFF_ITEMS, default='SMOOTH')
    random_seed: bpy.props.IntProperty(name='Random Seed', default=0, min=0, max=2147483647)
    control_count: bpy.props.IntProperty(
        name="Controls", default=8, min=4, max=64,
        description="Number of editable Bézier points around the entire mouth loop",
    )
    radius: bpy.props.FloatProperty(
        name="Influence Distance", default=0.0, min=0.0, subtype='DISTANCE',
        description="Distance along mesh edges; zero chooses a mouth-sized default at creation",
    )
    falloff: bpy.props.FloatProperty(
        name="Falloff", default=1.0, min=0.2, max=4.0,
        description="Higher values concentrate deformation closer to the selected loop",
    )
    shape_name: bpy.props.StringProperty(name="Shape Key Name", default="CurveMorph")
    reset_after: bpy.props.BoolProperty(
        name="Reset After Saving", default=True,
        description="Return controls to neutral so the next expression starts fresh",
    )
    target: bpy.props.PointerProperty(type=bpy.types.Object)


class CurveMorphState(bpy.types.PropertyGroup):
    falloff_type: bpy.props.EnumProperty(name='Falloff Type', items=FALLOFF_ITEMS, default='SMOOTH')
    random_seed: bpy.props.IntProperty(name='Random Seed', default=0, min=0, max=2147483647)
    twist_mask_group: bpy.props.StringProperty(name='Twist Mask',
        description='Optional vertex group affecting only Ctrl+T twist, not point movement; blank means no twist-only mask')
    setup_kind: bpy.props.EnumProperty(items=[('NONE', 'None', ''), ('LOOP', 'Loop', ''),
        ('CORNERS', 'Corners', ''), ('MASK', 'Mask', ''), ('PAINT', 'Paint', '')],
        default='NONE', options={'HIDDEN'})
    setup_editing: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    use_mask: bpy.props.BoolProperty(
        name="Limit to Vertex Group", default=False,
        description="Optional: multiply the distance falloff by this group's weights; unassigned vertices stay still",
    )
    mask_group: bpy.props.StringProperty(name="Influence Group")
    new_group_name: bpy.props.StringProperty(name="New Group", default="CurveMorph_Affected")
    mask_weight: bpy.props.FloatProperty(name="Assign Weight", default=1.0, min=0.0, max=1.0)
    token: bpy.props.StringProperty(options={'HIDDEN'})
    curve: bpy.props.PointerProperty(type=bpy.types.Object)
    preview_name: bpy.props.StringProperty(options={'HIDDEN'})
    control_count: bpy.props.IntProperty(name="Controls", default=8)
    radius: bpy.props.FloatProperty(
        name="Influence Distance", default=0.05, min=0.0, subtype='DISTANCE',
        description="Distance along connected mesh edges; zero moves only the selected loop",
    )
    falloff: bpy.props.FloatProperty(
        name="Falloff", default=1.0, min=0.2, max=4.0,
        description="Higher values concentrate deformation closer to the selected loop",
    )
    error: bpy.props.StringProperty(options={'HIDDEN'})
    last_saved_name: bpy.props.StringProperty(options={'HIDDEN'})


_CLASSES = (CurveMorphSetup, CurveMorphState)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.curvemorph_settings = bpy.props.PointerProperty(type=CurveMorphSetup)
    bpy.types.Object.curvemorph = bpy.props.PointerProperty(type=CurveMorphState)
    bpy.types.Object.curvemorph_target = bpy.props.PointerProperty(type=bpy.types.Object)
    operators.register()
    ui.register()
    session.register()
    corner_overlay.register()


def unregister():
    corner_overlay.unregister()
    session.unregister()
    ui.unregister()
    operators.unregister()
    del bpy.types.Object.curvemorph_target
    del bpy.types.Object.curvemorph
    del bpy.types.Scene.curvemorph_settings
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
