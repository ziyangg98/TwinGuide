import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy
from mathutils import Vector

from twin_guide import blender_ui
from twin_guide.blender.scene import clear_scene, set_active_object
from twin_guide.blender_ui_gizmos import TwinGuideFeatureGizmoGroup
from twin_guide.blender_ui_proxies import create_control
from twin_guide.config import EditorOverrides


class BlenderUiControlTests(unittest.TestCase):
    def setUp(self):
        clear_scene()
        self.previous_session = blender_ui._SESSION
        blender_ui._SESSION = SimpleNamespace(
            locked=False,
            dirty=True,
            begin_edit=Mock(),
        )
        bpy.utils.register_class(blender_ui.TwinGuideState)
        bpy.types.Scene.twin_guide_state = bpy.props.PointerProperty(type=blender_ui.TwinGuideState)

    def tearDown(self):
        blender_ui._SESSION = self.previous_session
        clear_scene()
        del bpy.types.Scene.twin_guide_state
        bpy.utils.unregister_class(blender_ui.TwinGuideState)

    @staticmethod
    def _control(kind, **properties):
        control = create_control(
            kind,
            Vector((0.0, 0.0, 0.0)),
            kind,
            properties,
        )
        control.hide_set(False)
        return control

    @staticmethod
    def _properties(control):
        return json.dumps(dict(control.items()), default=list, sort_keys=True)

    def _controls(self):
        candidates = json.dumps(
            [
                {
                    "fdi": 31,
                    "point": [0.0, 0.0, 0.0],
                    "tangent": {"x": 1.0, "y": 0.0, "z": 0.0},
                },
                {
                    "fdi": 32,
                    "point": [1.0, 0.0, 0.0],
                    "tangent": {"x": 1.0, "y": 0.0, "z": 0.0},
                },
            ]
        )
        return (
            self._control(
                "connector_node",
                guide_index=1,
                side="left",
                route_start=[0.0, 0.0, 0.0],
                route_end=[4.0, 0.0, 0.0],
                path_distance=2.0,
                down=[0.0, 0.0, -1.0],
            ),
            self._control(
                "junction",
                plane_origin=[0.0, 0.0, 0.0],
                plane_normal=[0.0, 0.0, 1.0],
            ),
            self._control(
                "observation_endpoint",
                window_id="test",
                role="start",
                fdi=31,
                candidates=candidates,
                axis_origin=[0.0, 0.0, 0.0],
            ),
            self._control(
                "observation_scalar",
                window_id="test",
                role="height",
                origin=[0.0, 0.0, 0.0],
                axis=[0.0, 1.0, 0.0],
            ),
            self._control(
                "sleeve_height",
                ring_index=1,
                role="platform",
                origin=[0.0, 0.0, 0.0],
                axis=[0.0, 0.0, 1.0],
            ),
            self._control(
                "surface_anchor",
                anchor_id="press_anchor_1",
                surface_role="template",
                normal=[0.0, 0.0, 1.0],
            ),
            self._control(
                "window_center",
                site_index=1,
                center=[0.0, 0.0, 0.0],
                tangent=[1.0, 0.0, 0.0],
                bitangent=[0.0, 1.0, 0.0],
                local_x=0.0,
                local_y=0.0,
            ),
            self._control(
                "window_margin",
                site_index=1,
                role="front",
                origin=[0.0, 0.0, 0.0],
                axis=[0.0, 0.0, 1.0],
            ),
            self._control(
                "window_size",
                site_index=1,
                role="width",
                origin=[0.0, 0.0, 0.0],
                axis=[1.0, 0.0, 0.0],
                value=2.0,
            ),
        )

    def test_every_semantic_control_enters_its_drag_operator(self):
        context = SimpleNamespace(
            active_object=None,
            scene=SimpleNamespace(twin_guide_state=SimpleNamespace(editing_locked=False)),
        )

        for control in self._controls():
            with self.subTest(kind=control["tg_kind"]):
                context.active_object = control
                before = self._properties(control)
                self.assertTrue(TwinGuideFeatureGizmoGroup.poll(context))
                if control["tg_kind"] == "surface_anchor":
                    self.assertTrue(blender_ui.TwinGuideSurfaceDragOperator.poll(context))
                else:
                    self.assertTrue(blender_ui._gizmo_axes(control))
                    self.assertTrue(blender_ui.TwinGuideHandleDragOperator.poll(context))
                self.assertEqual(self._properties(control), before)

    def test_sleeve_height_control_has_pickable_ring_surface(self):
        control = self._control(
            "sleeve_height",
            ring_index=1,
            role="platform",
            origin=[0.0, 0.0, 0.0],
            axis=[0.0, 0.0, 1.0],
        )

        self.assertEqual(len(control.data.polygons), 256)
        self.assertGreater(sum(polygon.area for polygon in control.data.polygons), 2.0)

    def test_sleeve_rotation_handle_moves_on_visible_circular_track(self):
        control = self._control(
            "sleeve_rotation",
            ring_index=1,
            role="rotation_guide_2",
            guide_number=2,
            center=[0.0, 0.0, 0.0],
            axis=[0.0, 0.0, 1.0],
            reference=[1.0, 0.0, 0.0],
            radius=2.0,
            pair_half_span=2.0,
            platform_height=1.0,
            total_height=3.0,
            angle_degrees=0.0,
        )
        self._control(
            "sleeve_rotation",
            ring_index=1,
            role="rotation_guide_1",
            guide_number=1,
            center=[0.0, 0.0, 0.0],
            axis=[0.0, 0.0, 1.0],
            reference=[-1.0, 0.0, 0.0],
            radius=2.0,
            pair_half_span=2.0,
            platform_height=1.0,
            total_height=3.0,
            angle_degrees=0.0,
        )
        set_active_object(control)

        blender_ui._update_sleeve_rotation_preview(1, 90.0)

        self.assertAlmostEqual(control.location.x, 0.0, places=6)
        self.assertAlmostEqual(control.location.y, 2.0, places=6)
        self.assertEqual(control["tg_feature_id"], "sleeve:site_1")
        self.assertEqual(blender_ui._semantic_values(control)[0][1], 90.0)
        self.assertTrue(blender_ui.TwinGuideSleeveRotationOperator.poll(bpy.context))

    def test_reference_visibility_toggles_template_and_dentition_independently(self):
        for role in ("template", "dentition"):
            mesh = bpy.data.meshes.new(f"{role}_mesh")
            object_ = bpy.data.objects.new(
                f"{blender_ui.SURFACE_PREFIX}{role}",
                mesh,
            )
            bpy.context.collection.objects.link(object_)

        state = SimpleNamespace(
            show_template_reference=False,
            show_dentition_reference=True,
        )
        blender_ui._reference_visibility_updated(state, SimpleNamespace())

        self.assertTrue(bpy.data.objects[f"{blender_ui.SURFACE_PREFIX}template"].hide_get())
        self.assertFalse(bpy.data.objects[f"{blender_ui.SURFACE_PREFIX}dentition"].hide_get())

    def test_every_axis_control_accepts_a_semantic_drag_value(self):
        with (
            patch.object(blender_ui, "_preview_feature_edit"),
            patch.object(blender_ui, "_update_connector_overlay"),
            patch.object(blender_ui, "_update_observation_overlay"),
            patch.object(blender_ui, "_update_press_overlay"),
            patch.object(blender_ui, "_update_sleeve_hint_label"),
            patch.object(blender_ui, "_update_window_overlay"),
        ):
            for control in self._controls():
                if control["tg_kind"] == "surface_anchor":
                    continue
                with self.subTest(kind=control["tg_kind"]):
                    set_active_object(control)
                    initial = blender_ui._gizmo_value(0)
                    blender_ui._gizmo_set_value(0, initial + 0.5)
                    self.assertAlmostEqual(
                        blender_ui._gizmo_value(0),
                        initial + 0.5,
                        places=5,
                    )

    def test_matching_preview_does_not_start_or_reload_geometry(self):
        mesh = bpy.data.meshes.new("preview_mesh")
        model = bpy.data.objects.new(blender_ui.PREVIEW_OBJECT_NAME, mesh)
        bpy.context.collection.objects.link(model)
        snapshot = {"geometry_fingerprint": "geometry-current"}
        session = SimpleNamespace(
            working_overrides=EditorOverrides(),
            revision=5,
        )
        state = bpy.context.scene.twin_guide_state

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                blender_ui,
                "_CONFIG",
                SimpleNamespace(output_directory=Path(directory)),
            ),
            patch.object(blender_ui, "_SESSION", session),
            patch.object(
                blender_ui,
                "_EDITOR_PLAN_VALUE",
                snapshot,
            ),
            patch.object(
                blender_ui,
                "replace",
                side_effect=lambda source, **values: SimpleNamespace(**(vars(source) | values)),
            ),
            patch.object(
                blender_ui,
                "editor_geometry_fingerprint",
                return_value="geometry-current",
            ),
            patch.object(
                blender_ui,
                "preview_directory",
                return_value=Path(directory),
            ),
            patch.object(
                blender_ui,
                "_matching_model_snapshot",
                return_value=(Path("model.stl"), Path("snapshot.json"), snapshot),
            ),
            patch.object(blender_ui, "_load_model") as load_model,
            patch.object(
                blender_ui,
                "_create_controls",
            ) as create_controls,
        ):
            reused = blender_ui._reuse_matching_preview()

            self.assertTrue(reused)
            load_model.assert_not_called()
            create_controls.assert_not_called()
            self.assertEqual(state.task_status, "已复用现有预览")

    def test_old_editor_snapshot_is_ignored_instead_of_crashing_ui_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "twin_guide.stl").write_text("placeholder", encoding="utf-8")
            (root / "ui-editor-snapshot.json").write_text(
                json.dumps({"schema_version": "twin-guide.ui-editor-snapshot/4.0"}),
                encoding="utf-8",
            )

            self.assertIsNone(blender_ui._matching_model_snapshot(root, "current"))

    def test_operation_size_handle_stays_on_parameter_plane(self):
        control = self._control(
            "window_size",
            site_index=1,
            role="width",
            origin=[0.0, 0.0, 2.0],
            axis=[1.0, 0.0, 0.0],
            value=1.0,
        )
        control.location = Vector((2.0, 3.0, 8.0))

        with patch.object(blender_ui, "_surface_point") as surface_point:
            blender_ui._constrain_control(control)

        surface_point.assert_not_called()
        self.assertEqual(control["tg_value"], 2.0)
        self.assertEqual(tuple(control.location), (2.0, 0.0, 2.0))


if __name__ == "__main__":
    unittest.main()
