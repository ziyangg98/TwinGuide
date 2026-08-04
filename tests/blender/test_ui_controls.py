import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy
from mathutils import Vector

from twin_guide import blender_ui
from twin_guide.blender.scene import clear_scene, set_active_object
from twin_guide.blender_ui_gizmos import TwinGuideFeatureGizmoGroup
from twin_guide.blender_ui_proxies import create_control


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
        bpy.types.Scene.twin_guide_state = bpy.props.PointerProperty(
            type=blender_ui.TwinGuideState
        )

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
                {"fdi": 31, "point": [0.0, 0.0, 0.0], "tangent": [1.0, 0.0, 0.0]},
                {"fdi": 32, "point": [1.0, 0.0, 0.0], "tangent": [1.0, 0.0, 0.0]},
            ]
        )
        return (
            self._control(
                "connector_node",
                guide_index=1,
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
                guide_index=1,
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
            scene=SimpleNamespace(
                twin_guide_state=SimpleNamespace(editing_locked=False)
            ),
        )

        for control in self._controls():
            with self.subTest(kind=control["tg_kind"]):
                context.active_object = control
                before = self._properties(control)
                self.assertTrue(TwinGuideFeatureGizmoGroup.poll(context))
                if control["tg_kind"] == "surface_anchor":
                    self.assertTrue(
                        blender_ui.TwinGuideSurfaceDragOperator.poll(context)
                    )
                else:
                    self.assertTrue(blender_ui._gizmo_axes(control))
                    self.assertTrue(
                        blender_ui.TwinGuideHandleDragOperator.poll(context)
                    )
                self.assertEqual(self._properties(control), before)

    def test_every_axis_control_accepts_a_semantic_drag_value(self):
        with patch.object(blender_ui, "_preview_feature_edit"), patch.object(
            blender_ui, "_update_connector_overlay"
        ), patch.object(blender_ui, "_update_observation_overlay"), patch.object(
            blender_ui, "_update_press_overlay"
        ), patch.object(blender_ui, "_update_sleeve_hint_label"), patch.object(
            blender_ui, "_update_window_overlay"
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


if __name__ == "__main__":
    unittest.main()
