"""检查 UI 导柱圆心旋转的纯几何约定。"""

import math
import unittest
from unittest.mock import patch

from twin_guide.case_analysis import _apply_sleeve_editor_override, _rotate_about_axis
from twin_guide.config import SleeveSiteOverride
from twin_guide.geometry import Vec3
from twin_guide.models import GuideSleeve
from twin_guide.sleeve_estimation.types import SleeveEstimate


class SleeveRotationTests(unittest.TestCase):
    @staticmethod
    def _guide(index: int, x: float) -> GuideSleeve:
        estimate = SleeveEstimate(
            axis_origin=Vec3(x, 0.0, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(1.0, 0.0, 0.0),
            height=15.5,
            platform_height=10.0,
            closed_bore_height=4.9,
            inner_radius=1.025,
            outer_radius=2.55,
            inner_arc_angle=math.radians(257.83),
            outer_arc_angle=math.radians(246.59),
            platform_slot_width=1.6,
        )
        return GuideSleeve(index, object(), estimate, 0.0, 15.5)

    def test_positive_rotation_uses_right_hand_rule_and_keeps_axis_component(self):
        rotated = _rotate_about_axis(
            Vec3(2.0, 0.0, 3.0),
            Vec3(0.0, 0.0, 1.0),
            math.radians(90.0),
        )

        self.assertAlmostEqual(rotated.x, 0.0, places=7)
        self.assertAlmostEqual(rotated.y, 2.0, places=7)
        self.assertAlmostEqual(rotated.z, 3.0, places=7)

    def test_pair_origins_axes_and_openings_rotate_together_about_midpoint(self):
        override = SleeveSiteOverride(1, 15.5, 10.0, 4.9, 90.0)
        with patch(
            "twin_guide.case_analysis.create_closed_sleeve_object",
            side_effect=lambda _parameters, name: name,
        ):
            rotated = _apply_sleeve_editor_override(
                (self._guide(1, -2.0), self._guide(2, 2.0)),
                override,
            )

        self.assertAlmostEqual(rotated[0].center.x, 0.0, places=7)
        self.assertAlmostEqual(rotated[0].center.y, -2.0, places=7)
        self.assertAlmostEqual(rotated[1].center.x, 0.0, places=7)
        self.assertAlmostEqual(rotated[1].center.y, 2.0, places=7)
        for guide in rotated:
            self.assertAlmostEqual(guide.axis.z, 1.0, places=7)
            self.assertAlmostEqual(guide.parameters.c_opening_direction.x, 0.0, places=7)
            self.assertAlmostEqual(guide.parameters.c_opening_direction.y, 1.0, places=7)


if __name__ == "__main__":
    unittest.main()
