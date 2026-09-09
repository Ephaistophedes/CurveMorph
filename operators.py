"""Small UI actions for the Bézier mouth posing session."""

import traceback

import bpy

from . import session, setup


def _target(context):
    mesh = session.resolve_target(context)
    if mesh is None or not mesh.curvemorph.token:
        raise ValueError("No active mouth pose. Select a mouth edge loop and create controls first.")
    return mesh


def _failed(operator, error):
    operator.report({'ERROR'}, str(error) or type(error).__name__)
    if not isinstance(error, (ValueError, RuntimeError, ReferenceError)):
        traceback.print_exc()
    return {'CANCELLED'}


class _SessionOperator:
    @classmethod
    def poll(cls, context):
        mesh = session.resolve_target(context)
        if mesh is None or not mesh.curvemorph.token:
            cls.poll_message_set("Create mouth controls first")
            return False
        return True


class CURVEMORPH_OT_create(bpy.types.Operator):
    bl_idname = "curvemorph.create"
    bl_label = "Create Mouth Controls"
    bl_description = "Create controls using the stored mouth loop and two corner vertices"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = setup.resolve_target(context)
        stored = setup.read(obj)
        if obj is None or not stored or len(stored.get('corners', [])) != 2:
            cls.poll_message_set("Store the mouth loop and two corner vertices in Setup first")
            return False
        if obj.curvemorph.token:
            cls.poll_message_set("Finish this mesh's current mouth pose session first")
            return False
        return True

    def execute(self, context):
        settings = context.scene.curvemorph_settings
        try:
            mesh = setup.create_controls(context)
        except Exception as error:
            return _failed(self, error)
        # The session already exists; retain a successful undo step even if
        # entering Edit Mode is unavailable in this particular editor context.
        try:
            session.edit_controls(context, mesh)
        except (ValueError, RuntimeError, ReferenceError) as error:
            self.report({'WARNING'}, f"Controls created. Use Edit Controls to begin: {error}")
        else:
            self.report({'INFO'}, "Move a control with G; save the pose as a shape key when ready")
        return {'FINISHED'}


class CURVEMORPH_OT_edit(_SessionOperator, bpy.types.Operator):
    bl_idname = "curvemorph.edit"
    bl_label = "Edit Controls"
    bl_description = "Select the Bézier curve and enter Edit Mode; G moves points, V changes handle types"
    bl_options = {'UNDO'}

    def execute(self, context):
        try:
            session.edit_controls(context, _target(context))
        except Exception as error:
            return _failed(self, error)
        return {'FINISHED'}


class CURVEMORPH_OT_reset(_SessionOperator, bpy.types.Operator):
    bl_idname = "curvemorph.reset"
    bl_label = "Reset Pose"
    bl_description = "Return the mouth controls to their starting shape; saved shape keys remain"
    bl_options = {'UNDO'}

    def execute(self, context):
        try:
            session.reset_pose(_target(context))
        except Exception as error:
            return _failed(self, error)
        return {'FINISHED'}


class CURVEMORPH_OT_symmetrize(_SessionOperator, bpy.types.Operator):
    bl_idname = 'curvemorph.symmetrize'
    bl_label = 'Symmetrize CurveMorph'
    bl_description = 'Copy the chosen side across mesh-local X=0, including handles; tangents become Free to preserve the exact pose'
    bl_options = {'UNDO'}
    direction: bpy.props.EnumProperty(items=[
        ('POSITIVE', '+X → −X', 'Keep positive X and mirror it onto negative X'),
        ('NEGATIVE', '−X → +X', 'Keep negative X and mirror it onto positive X'),
    ])

    def execute(self, context):
        try:
            session.symmetrize_pose(_target(context), self.direction)
        except Exception as error:
            return _failed(self, error)
        self.report({'INFO'}, 'Pose symmetrized. Edit Controls to continue; V > Automatic restores auto handles.')
        return {'FINISHED'}


class CURVEMORPH_OT_rebuild(_SessionOperator, bpy.types.Operator):
    bl_idname = "curvemorph.rebuild"
    bl_label = "Rebuild Controls"
    bl_description = "Change the number of controls and reset the current pose; saved shape keys remain"
    bl_options = {'UNDO'}

    control_count: bpy.props.IntProperty(
        name="Control Points", default=8, min=4, max=64,
        description="Number of Bézier control points around the selected mouth loop",
    )

    def invoke(self, context, event):
        try:
            _target(context)
        except Exception as error:
            return _failed(self, error)
        self.control_count = context.scene.curvemorph_settings.control_count
        return context.window_manager.invoke_props_dialog(
            self, width=360, title="Rebuild Mouth Controls", confirm_text="Rebuild",
        )

    def draw(self, context):
        self.layout.label(text="This resets the current unsaved mouth pose.", icon='INFO')
        self.layout.label(text="Saved shape keys remain available.")
        self.layout.prop(self, "control_count")

    def execute(self, context):
        try:
            session.rebuild_controls(_target(context), self.control_count)
            context.scene.curvemorph_settings.control_count = self.control_count
        except Exception as error:
            return _failed(self, error)
        return {'FINISHED'}


class CURVEMORPH_OT_save(_SessionOperator, bpy.types.Operator):
    bl_idname = "curvemorph.save"
    bl_label = "Save Shape Key"
    bl_description = "Save the current mouth pose as a new shape key without applying the mesh's modifiers"
    bl_options = {'UNDO'}

    def execute(self, context):
        settings = context.scene.curvemorph_settings
        name = settings.shape_name.strip()
        if not name:
            self.report({'ERROR'}, "Enter a name for the shape key")
            return {'CANCELLED'}
        try:
            key = session.save_shape_key(_target(context), name, settings.reset_after)
        except Exception as error:
            return _failed(self, error)
        self.report({'INFO'}, f'Saved shape key "{key.name}"')
        return {'FINISHED'}


class CURVEMORPH_OT_finish(_SessionOperator, bpy.types.Operator):
    bl_idname = "curvemorph.finish"
    bl_label = "Finish Session"
    bl_description = "Remove the controls and discard the temporary preview; saved shape keys remain"
    bl_options = {'UNDO'}

    def invoke(self, context, event):
        try:
            _target(context)
        except Exception as error:
            return _failed(self, error)
        return context.window_manager.invoke_confirm(
            self, event, title="Finish CurveMorph?", confirm_text="Finish",
            message="Discard the live preview and remove its controls. Saved shape keys remain.",
            icon='QUESTION',
        )

    def execute(self, context):
        try:
            session.finish_session(_target(context))
        except Exception as error:
            return _failed(self, error)
        self.report({'INFO'}, "Mouth controls removed; saved shape keys remain")
        return {'FINISHED'}


class CURVEMORPH_OT_refresh(_SessionOperator, bpy.types.Operator):
    bl_idname = "curvemorph.refresh"
    bl_label = "Refresh Preview"
    bl_description = "Recalculate the mouth preview from the current controls and influence settings"
    bl_options = {'UNDO'}

    def execute(self, context):
        try:
            mesh = _target(context)
            session.update_session(mesh, force=True)
            if mesh.curvemorph.error:
                raise ValueError(mesh.curvemorph.error)
        except Exception as error:
            return _failed(self, error)
        return {'FINISHED'}


class _SetupOperator:
    @classmethod
    def poll(cls, context):
        return setup.resolve_target(context) is not None


class CURVEMORPH_OT_setup_edit(_SetupOperator, bpy.types.Operator):
    bl_idname = 'curvemorph.setup_edit'
    bl_label = 'Refine Setup Selection'
    bl_description = 'Show the source mesh at neutral to refine the setup; current control edits are kept'
    bl_options = {'UNDO'}
    kind: bpy.props.EnumProperty(items=[('LOOP', 'Loop', ''), ('CORNERS', 'Corners', ''),
                                       ('MASK', 'Affected Vertices', ''), ('PAINT', 'Paint Mask', '')])

    def execute(self, context):
        try:
            setup.begin_edit(context, setup.resolve_target(context), self.kind)
        except Exception as error:
            return _failed(self, error)
        return {'FINISHED'}


class CURVEMORPH_OT_auto_corners(bpy.types.Operator):
    bl_idname = 'curvemorph.auto_corners'
    bl_label = 'Auto Corners'
    bl_description = 'Select the stored loop vertices at minimum and maximum mesh-local X; click Store Corners to confirm'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        stored = setup.read(setup.resolve_target(context))
        if not stored or not stored.get('loop'):
            cls.poll_message_set('Store the mouth loop first')
            return False
        return True

    def execute(self, context):
        try:
            setup.select_auto_corners(context)
        except Exception as error:
            return _failed(self, error)
        self.report({'INFO'}, 'Local −X / +X corners selected. Click Store Corners to confirm.')
        return {'FINISHED'}


class CURVEMORPH_OT_setup_capture(bpy.types.Operator):
    bl_idname = 'curvemorph.setup_capture'
    bl_label = 'Store Setup Selection'
    bl_description = 'Store selected edges as the loop, or exactly two selected loop vertices as the corners'
    bl_options = {'UNDO'}
    kind: bpy.props.EnumProperty(items=[('LOOP', 'Mouth Loop', ''), ('CORNERS', 'Corner Vertices', '')])

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH' and context.mode == 'EDIT_MESH'

    def execute(self, context):
        try:
            mesh = setup.capture_loop(context) if self.kind == 'LOOP' else setup.capture_corners(context)
        except Exception as error:
            return _failed(self, error)
        self.report({'INFO'}, 'Loop stored. Choose its two corner vertices next.' if self.kind == 'LOOP'
                    else 'Corners stored. Optionally choose an influence group, then create or apply controls.')
        return {'FINISHED'}


class CURVEMORPH_OT_mask_selection(bpy.types.Operator):
    bl_idname = 'curvemorph.mask_selection'
    bl_label = 'Update Influence Group'
    bl_description = 'Create a new group from the selected vertices, or assign/remove vertices in the chosen group'
    bl_options = {'UNDO'}
    action: bpy.props.EnumProperty(items=[('NEW', 'New Group', ''), ('ASSIGN', 'Assign Selected', ''), ('REMOVE', 'Remove Selected', '')])

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH' and context.mode == 'EDIT_MESH'

    def execute(self, context):
        try:
            group = setup.create_mask(context, self.action)
        except Exception as error:
            return _failed(self, error)
        self.report({'INFO'}, f'Influence group: {group.name}')
        return {'FINISHED'}


class CURVEMORPH_OT_setup_apply(_SessionOperator, bpy.types.Operator):
    bl_idname = 'curvemorph.setup_apply'
    bl_label = 'Apply Setup'
    bl_description = 'Rebuild neutral controls from the stored loop and corners; saved shape keys stay intact'
    bl_options = {'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title='Apply Refined Setup?', confirm_text='Apply Setup',
            message='This resets the current unsaved pose. Saved shape keys are kept.', icon='QUESTION')

    def execute(self, context):
        try:
            setup.apply_setup(context, _target(context))
        except Exception as error:
            return _failed(self, error)
        self.report({'INFO'}, 'Setup applied. Click Edit Controls to pose the mouth.')
        return {'FINISHED'}


class CURVEMORPH_OT_setup_discard(_SessionOperator, bpy.types.Operator):
    bl_idname = 'curvemorph.setup_discard'
    bl_label = 'Discard Loop/Corner Changes'
    bl_description = 'Restore the currently applied loop and corners without resetting the pose or changing the mask'
    bl_options = {'UNDO'}

    def execute(self, context):
        try:
            setup.discard_changes(_target(context))
        except Exception as error:
            return _failed(self, error)
        return {'FINISHED'}


_CLASSES = (
    CURVEMORPH_OT_setup_edit, CURVEMORPH_OT_auto_corners, CURVEMORPH_OT_setup_capture, CURVEMORPH_OT_mask_selection,
    CURVEMORPH_OT_setup_apply, CURVEMORPH_OT_setup_discard,
    CURVEMORPH_OT_create, CURVEMORPH_OT_edit, CURVEMORPH_OT_reset, CURVEMORPH_OT_symmetrize,
    CURVEMORPH_OT_rebuild, CURVEMORPH_OT_save, CURVEMORPH_OT_finish,
    CURVEMORPH_OT_refresh,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
