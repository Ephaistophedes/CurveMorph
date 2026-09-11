"""CurveMorph's focused creation and posing workflows."""
import textwrap
import bpy
from . import setup, facial


def face_action(layout, action, text, icon='NONE'):
    layout.operator('facepose.action', text=text, icon=icon).action = action


def section(layout, identifier, title, closed=False):
    header, body = layout.panel(identifier, default_closed=closed)
    header.label(text=title)
    if body:
        body.use_property_split = True
        body.use_property_decorate = False
    return body


def fitting(mesh, entry):
    return mesh.curvemorph.fit_editing if entry.legacy else entry.fitting


def draw_error(layout, context, message):
    box = layout.box()
    box.alert = True
    box.label(text='Preview needs attention', icon='ERROR')
    scale = context.preferences.system.ui_scale
    width = max(22, int(context.region.width / max(7 * scale, 1)) - 7)
    for line in textwrap.wrap(message, width):
        box.label(text=line)
    face_action(box, 'REFRESH', 'Refresh Preview', 'FILE_REFRESH')


class FACEPOSE_UL_curves(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        split = layout.split(factor=.58)
        name = split.row(align=True)
        if item.legacy:
            name.label(text='', icon='CHECKBOX_HLT')
            state = data.curvemorph
            group = state.mask_group if state.use_mask else ''
        else:
            name.prop(item, 'enabled', text='', emboss=False,
                      icon='CHECKBOX_HLT' if item.enabled else 'CHECKBOX_DEHLT')
            state, group = item, item.mask_group
        name.prop(item, 'name', text='', emboss=False,
                  icon='ERROR' if state.error else 'CURVE_BEZCURVE')
        column = split.row()
        column.active = bool(group)
        column.label(text=group or '—')


class FACEPOSE_OT_workspace(bpy.types.Operator):
    """Switch UI views without modifying geometry or creating an undo step."""
    bl_idname = 'facepose.workspace'
    bl_label = 'Add Control'
    bl_description = 'Choose a creation method and add another facial control'
    mode: bpy.props.EnumProperty(items=[('POSE', 'Pose', ''), ('ADD', 'Add Controls', '')], default='ADD')

    def execute(self, context):
        context.scene.face_pose_settings.ui_mode = self.mode
        return {'FINISHED'}


def draw_creation(layout, context, mesh):
    settings = context.scene.face_pose_settings
    form = layout.column()
    form.use_property_split = True
    form.use_property_decorate = False
    form.prop(settings, 'source', text='From')
    layout.separator()
    if settings.source == 'MOUTH':
        draw_mouth_selection(layout, mesh)
        mouth = context.scene.curvemorph_settings
        if not mesh.curvemorph.token:
            form = layout.column()
            form.use_property_split = True
            form.use_property_decorate = False
            form.prop(mouth, 'control_count', text='Points')
            form.prop(mouth, 'radius', text='Influence')
            row = layout.row()
            row.scale_y = 1.25
            row.operator('curvemorph.create', text='Create Mouth Control', icon='ADD')
        else:
            layout.label(text='This mesh already has a mouth control.', icon='INFO')
            layout.operator('facepose.workspace', text='Back to Pose', icon='BACK').mode = 'POSE'
        return
    form = layout.column()
    form.use_property_split = True
    form.use_property_decorate = False
    form.prop(settings, 'name', text='Name')
    form.prop(settings, 'count', text='Points')
    form.prop(settings, 'radius', text='Influence')
    if settings.source == 'GREASE':
        form.prop(settings, 'grease', text='Drawing')
    if settings.radius == 0:
        layout.label(text='Influence: automatic starting distance.', icon='INFO')
    layout.separator()
    if settings.source == 'EDGES':
        selecting = context.active_object == mesh and mesh.mode == 'EDIT'
        row = layout.row()
        row.enabled = not selecting
        face_action(row, 'SELECT_MESH', 'Select Mesh Edges', 'EDITMODE_HLT')
        layout.label(text='Select one open chain or closed loop.')
        row = layout.row()
        row.enabled = selecting
        row.scale_y = 1.25
        row.operator('facepose.add_edges', text='Create Control', icon='ADD')
    else:
        layout.label(text='Current frame · one control per stroke')
        row = layout.row()
        row.enabled = bool(settings.grease or (context.active_object and context.active_object.type == 'GREASEPENCIL'))
        row.scale_y = 1.25
        row.operator('facepose.add_strokes', text='Create from Strokes', icon='GREASEPENCIL')


def draw_pose(layout, context, mesh):
    settings = context.scene.face_pose_settings
    if not mesh.face_pose_curves:
        box = layout.box()
        box.label(text='No controls yet', icon='CURVE_BEZCURVE')
        box.label(text='Add a curve to start posing this mesh.')
        row = box.row()
        row.scale_y = 1.3
        row.operator('facepose.workspace', text='Add Your First Control', icon='ADD')
        return
    entry = facial.active(mesh)
    state = mesh.curvemorph if entry.legacy else entry
    is_fitting = fitting(mesh, entry)
    paused = entry.legacy and state.setup_editing
    header = layout.row()
    split = header.split(factor=.58)
    split.label(text='Control')
    split.label(text='Vertex group')
    header.separator(factor=1.4)
    row = layout.row()
    row.template_list('FACEPOSE_UL_curves', '', mesh, 'face_pose_curves', mesh, 'face_pose_index', rows=4)
    toolbar = row.column(align=True)
    toolbar.operator('facepose.workspace', text='', icon='ADD')
    remove = toolbar.column(align=True)
    remove.enabled = not is_fitting
    face_action(remove, 'REMOVE', '', 'REMOVE')
    if state.error:
        draw_error(layout, context, state.error)
    if is_fitting:
        box = layout.box()
        box.label(text='Adjusting the neutral fit', icon='CURVE_BEZCURVE')
        box.label(text='This control’s deformation is paused.')
        box.label(text='Subdivide to add points; X to delete.')
        face_action(box, 'EDIT', 'Continue Editing Fit', 'EDITMODE_HLT')
        edit = box.row(align=True)
        edit.enabled = bool(entry.curve and context.active_object == entry.curve and entry.curve.mode == 'EDIT')
        edit.operator('curve.subdivide', text='Subdivide Selected', icon='ADD')
        edit.operator('curve.delete', text='Delete Selected', icon='REMOVE').type = 'VERT'
        row = box.row(align=True)
        face_action(row, 'APPLY_FIT', 'Apply Fit', 'CHECKMARK')
        face_action(row, 'CANCEL_FIT', 'Cancel', 'X')
        return
    if paused:
        layout.label(text='Mouth setup is paused at neutral.', icon='PAUSE')
    row = layout.row(align=True)
    row.scale_y = 1.2
    face_action(row, 'EDIT', 'Resume Pose' if paused else 'Edit Pose', 'EDITMODE_HLT')
    reset = row.row(align=True)
    reset.enabled = not paused
    face_action(reset, 'RESET', 'Reset', 'LOOP_BACK')
    row = layout.row(align=True)
    row.enabled = not paused
    face_action(row, 'MIRROR', 'Mirror ' + settings.axis, 'MOD_MIRROR')
    row.popover(panel='FACEPOSE_PT_mirror', text='', icon='PREFERENCES')

    body = section(layout, 'curvemorph_influence', 'Influence')
    if body:
        body.prop(state, 'radius', text='Distance')
        draw_falloff(body, state)
        if entry.legacy:
            body.prop(state, 'use_mask', text='Use Vertex Group')
            if state.use_mask:
                body.prop_search(state, 'mask_group', mesh, 'vertex_groups', text='Group')
        else:
            body.prop_search(entry, 'mask_group', mesh, 'vertex_groups', text='Group')
    body = section(layout, 'curvemorph_save', 'Save Pose')
    if body:
        body.prop(settings, 'shape_name', text='Name')
        body.prop(settings, 'only_active', text='Active Control Only')
        body.prop(settings, 'reset_after', text='Reset After Saving')
        row = body.row()
        row.enabled = not paused and not any(fitting(mesh, item) for item in mesh.face_pose_curves)
        row.scale_y = 1.25
        face_action(row, 'SAVE', 'Save Shape Key', 'SHAPEKEY_DATA')
    body = section(layout, 'curvemorph_advanced', 'Advanced', closed=True)
    if body:
        body.prop_search(state, 'twist_mask_group', mesh, 'vertex_groups', text='Twist Mask')
        row = body.row()
        row.enabled = not paused
        face_action(row, 'FIT', 'Adjust Neutral Fit', 'CURVE_BEZCURVE')
        row = body.row(align=True)
        face_action(row, 'REFRESH', 'Refresh', 'FILE_REFRESH')
        face_action(row, 'RESET_ALL', 'Reset All', 'LOOP_BACK')
        if entry.curve:
            raw = entry.curve.get(facial.RAW) if not entry.legacy else None
            closed = bool(raw['closed']) if raw else True
            count = len(entry.curve.data.splines[0].bezier_points) if entry.curve.data.splines else 0
            body.label(text=f'{"Closed loop" if closed else "Open curve"} · {count} points')
        body.label(text='G: move · V: handles · Ctrl+T: tilt')


class FACEPOSE_PT_main(bpy.types.Panel):
    bl_label = 'CurveMorph'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'CurveMorph'
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        layout.use_property_decorate = False
        settings = context.scene.face_pose_settings
        mesh = facial.target(context)
        layout.prop(context.scene.curvemorph_settings, 'target', text='Mesh')
        if mesh is None:
            layout.label(text='Choose your character mesh.', icon='INFO')
            return
        if context.scene.curvemorph_settings.target != mesh:
            layout.label(text='Active mesh: ' + mesh.name, icon='MESH_DATA')
        layout.prop(settings, 'ui_mode', expand=True)
        layout.separator(factor=.5)
        if settings.ui_mode == 'ADD':
            draw_creation(layout, context, mesh)
        else:
            draw_pose(layout, context, mesh)


class FACEPOSE_PT_mirror(bpy.types.Panel):
    """Popover shown only from the settings button next to Mirror."""
    bl_label = 'Mirror Settings'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        settings = context.scene.face_pose_settings
        layout.label(text='Mirror Settings', icon='MOD_MIRROR')
        layout.prop(settings, 'axis', expand=True)
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(settings, 'tolerance', text='Match Distance')
        layout.prop(settings, 'mirror_groups', text='Copy Groups')
        layout.label(text='Across the mesh’s local origin.')


def draw_mouth_selection(layout, mesh):
    stored = setup.read(mesh)
    count = len(stored['loop']) if stored else 0
    corners = list(stored.get('corners', [])) if stored else []
    layout.label(text=f'1. Edge loop · {count} vertices' if count else '1. Select the mouth edge loop',
                 icon='CHECKMARK' if count else 'MESH_DATA')
    row = layout.row(align=True)
    row.operator('curvemorph.setup_edit', text='Select Edges').kind = 'LOOP'
    row.operator('curvemorph.setup_capture', text='Store Loop').kind = 'LOOP'
    layout.label(text='2. Two corners stored' if len(corners) == 2 else '2. Choose two mouth corners',
                 icon='CHECKMARK' if len(corners) == 2 else 'VERTEXSEL')
    row = layout.row(align=True)
    row.enabled = bool(count)
    row.operator('curvemorph.setup_edit', text='Select').kind = 'CORNERS'
    row.operator('curvemorph.auto_corners', text='Auto')
    row.operator('curvemorph.setup_capture', text='Store').kind = 'CORNERS'
    if mesh.mode == 'EDIT' and mesh.curvemorph.setup_kind == 'CORNERS':
        layout.label(text='Pick two vertices on the cyan guide.', icon='INFO')
    if mesh.curvemorph.token:
        if setup.pending(mesh):
            box = layout.box()
            box.label(text='Unapplied loop / corner changes', icon='INFO')
            box.operator('curvemorph.setup_apply', text='Apply Setup', icon='FILE_REFRESH')
            box.operator('curvemorph.setup_discard', text='Discard Changes')
        if mesh.curvemorph.setup_editing:
            layout.operator('curvemorph.edit', text='Resume Current Pose', icon='EDITMODE_HLT')


class CURVEMORPH_PT_main(bpy.types.Panel):
    """Original corner and weight-editing tools, only for the selected mouth."""
    bl_label = 'Mouth Setup'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'CurveMorph'
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 1

    @classmethod
    def poll(cls, context):
        mesh = facial.target(context)
        return bool(mesh and mesh.face_pose_curves and context.scene.face_pose_settings.ui_mode == 'POSE'
                    and facial.active(mesh).legacy and not mesh.curvemorph.fit_editing)

    def draw(self, context):
        layout = self.layout
        layout.use_property_decorate = False
        mesh = facial.target(context)
        state = mesh.curvemorph
        settings = context.scene.curvemorph_settings
        draw_mouth_selection(layout, mesh)
        body = section(layout, 'curvemorph_mouth_mask', 'Edit Vertex Group', closed=True)
        if body:
            body.prop(state, 'use_mask', text='Use Vertex Group')
            if state.use_mask:
                body.prop_search(state, 'mask_group', mesh, 'vertex_groups', text='Group')
                row = body.row(align=True)
                row.operator('curvemorph.setup_edit', text='Select Affected').kind = 'MASK'
                row.operator('curvemorph.setup_edit', text='Weight Paint').kind = 'PAINT'
                body.prop(state, 'new_group_name', text='New Group')
                body.operator('curvemorph.mask_selection', text='Create from Selection').action = 'NEW'
                body.prop(state, 'mask_weight', text='Weight')
                row = body.row(align=True)
                row.operator('curvemorph.mask_selection', text='Assign').action = 'ASSIGN'
                row.operator('curvemorph.mask_selection', text='Remove').action = 'REMOVE'
        body = section(layout, 'curvemorph_mouth_rebuild', 'Rebuild & Symmetry', closed=True)
        if body:
            body.prop(settings, 'control_count', text='Points')
            body.operator('curvemorph.rebuild', text='Rebuild Controls', icon='FILE_REFRESH').control_count = settings.control_count
            body.label(text='Rebuilding resets the current pose.', icon='INFO')
            row = body.row(align=True)
            row.enabled = not state.setup_editing
            row.operator('curvemorph.symmetrize', text='+X → −X').direction = 'POSITIVE'
            row.operator('curvemorph.symmetrize', text='−X → +X').direction = 'NEGATIVE'


def draw_falloff(layout, settings):
    layout.prop(settings, 'falloff_type', text='Falloff')
    row = layout.row()
    row.enabled = settings.falloff_type != 'CONSTANT'
    row.prop(settings, 'falloff', text='Strength')
    if settings.falloff_type == 'RANDOM':
        layout.prop(settings, 'random_seed', text='Seed')


CLASSES = (FACEPOSE_UL_curves, FACEPOSE_OT_workspace, FACEPOSE_PT_main, FACEPOSE_PT_mirror, CURVEMORPH_PT_main)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
