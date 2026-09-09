"""The select-loop, pose-controls, save-shape-key workflow."""

import textwrap

import bpy

from . import setup


def draw_setup(layout, mesh):
    box = layout.box()
    box.label(text='Setup — revisit any time', icon='PREFERENCES')
    if mesh is None:
        box.label(text='Select the character mesh first.', icon='INFO')
        return
    stored = setup.read(mesh)
    loop_count = len(stored['loop']) if stored else 0
    corners = list(stored.get('corners', [])) if stored else []
    box.label(text=f'1. Mouth loop: {loop_count} vertices' if loop_count else '1. Store a mouth edge loop',
              icon='CHECKMARK' if loop_count else 'MESH_DATA')
    row = box.row(align=True)
    row.operator('curvemorph.setup_edit', text='Select / Refine Loop').kind = 'LOOP'
    row.operator('curvemorph.setup_capture', text='Store Loop').kind = 'LOOP'
    box.label(text='2. Corners: ' + ', '.join(str(i) for i in corners) if len(corners) == 2 else '2. Choose two corner vertices',
              icon='CHECKMARK' if len(corners) == 2 else 'VERTEXSEL')
    row = box.row(align=True)
    row.enabled = bool(loop_count)
    row.operator('curvemorph.setup_edit', text='Select / Refine Corners').kind = 'CORNERS'
    row.operator('curvemorph.auto_corners', text='Auto')
    row.operator('curvemorph.setup_capture', text='Store Corners').kind = 'CORNERS'
    if mesh.mode == 'EDIT' and mesh.curvemorph.setup_kind == 'CORNERS':
        box.label(text='Cyan guide: stored loop. Pick two vertices.', icon='INFO')
    box.separator()
    box.label(text='3. Affected vertices (optional)', icon='GROUP_VERTEX')
    pose = mesh.curvemorph
    box.prop(pose, 'use_mask')
    if pose.use_mask:
        box.prop_search(pose, 'mask_group', mesh, 'vertex_groups', text='Group')
        row = box.row(align=True)
        row.operator('curvemorph.setup_edit', text='Select Affected').kind = 'MASK'
        row.operator('curvemorph.setup_edit', text='Weight Paint').kind = 'PAINT'
        box.prop(pose, 'new_group_name', text='New Group')
        box.operator('curvemorph.mask_selection', text='Create Group from Selection').action = 'NEW'
        box.prop(pose, 'mask_weight')
        row = box.row(align=True)
        row.operator('curvemorph.mask_selection', text='Assign Selected').action = 'ASSIGN'
        row.operator('curvemorph.mask_selection', text='Remove Selected').action = 'REMOVE'
        box.label(text='Weight 0: fixed. Weight 1: full influence.')
    else:
        box.label(text='Skipped: use distance falloff only.')
    box.separator()
    box.prop_search(pose, 'twist_mask_group', mesh, 'vertex_groups', text='Twist Mask')
    box.label(text='Twist only. Leave blank for no extra mask.')
    if pose.token:
        if setup.pending(mesh):
            box.label(text='Loop / corner changes not applied.', icon='INFO')
            box.operator('curvemorph.setup_apply', text='Apply Setup (Reset Pose)', icon='FILE_REFRESH')
            box.operator('curvemorph.setup_discard')
        if pose.setup_editing:
            box.label(text='Preview paused at neutral for setup.', icon='PAUSE')
            box.operator('curvemorph.edit', text='Resume Current Controls', icon='EDITMODE_HLT')


class CURVEMORPH_PT_main(bpy.types.Panel):
    bl_label = "CurveMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CurveMorph"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.curvemorph_settings
        mesh = setup.resolve_target(context)
        draw_setup(layout, mesh)
        if mesh is None or not mesh.curvemorph.token:
            layout.label(text="Create from stored loop and corners.", icon='CURVE_BEZCURVE')
            column = layout.column(align=True)
            column.prop(settings, "control_count", text="Control Points")
            column.prop(settings, "radius", text="Influence Distance")
            draw_falloff(column, settings)
            layout.label(text="Distance 0 chooses a starting size.", icon='INFO')
            layout.operator("curvemorph.create", icon='CURVE_BEZCURVE')
            return

        pose = mesh.curvemorph
        layout.label(text=mesh.name, icon='MESH_DATA')
        if pose.error:
            box = layout.box()
            box.alert = True
            box.label(text="Preview needs attention", icon='ERROR')
            for line in textwrap.wrap(pose.error, width=42):
                box.label(text=line)
            box.operator("curvemorph.refresh", icon='FILE_REFRESH')

        row = layout.row(align=True)
        row.operator("curvemorph.edit", icon='EDITMODE_HLT')
        row.operator("curvemorph.reset", text="Reset", icon='LOOP_BACK')
        layout.label(text="G: move   V: Aligned / Free handles", icon='INFO')
        layout.label(text='Ctrl+T: tilt lips   Alt+T: clear tilt', icon='INFO')
        layout.label(text='Symmetrize pose · mesh local X')
        row = layout.row(align=True)
        row.enabled = not pose.setup_editing
        row.operator('curvemorph.symmetrize', text='+X → −X').direction = 'POSITIVE'
        row.operator('curvemorph.symmetrize', text='−X → +X').direction = 'NEGATIVE'

        column = layout.column(align=True)
        column.prop(pose, "radius", text="Influence Distance")
        draw_falloff(column, pose)
        layout.label(text="Distance follows connected mesh edges.")

        layout.separator()
        row = layout.row(align=True)
        row.prop(settings, "control_count", text="Controls")
        rebuild = row.operator("curvemorph.rebuild", text="Rebuild")
        rebuild.control_count = settings.control_count
        layout.label(text=f"Current: {pose.control_count} controls; rebuild resets pose.")

        layout.separator()
        column = layout.column(align=True)
        column.prop(settings, "shape_name", text="Name")
        column.prop(settings, "reset_after", text="Reset Controls After Saving")
        column.operator("curvemorph.save", icon='SHAPEKEY_DATA')
        if pose.last_saved_name:
            layout.label(text=f"Saved: {pose.last_saved_name}", icon='CHECKMARK')

        layout.separator()
        layout.operator("curvemorph.finish", icon='CHECKMARK')


def draw_falloff(layout, settings):
    layout.prop(settings, 'falloff_type')
    row = layout.row()
    row.enabled = settings.falloff_type != 'CONSTANT'
    row.prop(settings, 'falloff', text='Falloff Strength')
    if settings.falloff_type == 'RANDOM':
        layout.prop(settings, 'random_seed')


def register():
    bpy.utils.register_class(CURVEMORPH_PT_main)


def unregister():
    bpy.utils.unregister_class(CURVEMORPH_PT_main)
