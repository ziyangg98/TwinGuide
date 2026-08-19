"""末端远中公共节点的后端无关牙弓方向测试。"""

import unittest

import numpy as np

from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.terminal_distal_common_node import _terminal_arch_direction


class TerminalArchDirectionTests(unittest.TestCase):
    def test_terminal_slot_before_neighbor_reverses_increasing_arch_tangent(self) -> None:
        direction = _terminal_arch_direction((17, 16, 15, 14), 17, 16, Vec3(1.0, 0.0, 0.0))

        np.testing.assert_allclose(direction, [-1.0, 0.0, 0.0])

    def test_terminal_slot_after_neighbor_keeps_increasing_arch_tangent(self) -> None:
        direction = _terminal_arch_direction((14, 15, 16, 17), 17, 16, Vec3(1.0, 0.0, 0.0))

        np.testing.assert_allclose(direction, [1.0, 0.0, 0.0])

    def test_two_implant_slots_must_be_contiguous(self) -> None:
        direction = _terminal_arch_direction(
            (18, 17, 16, 15),
            18,
            16,
            Vec3(0.0, 1.0, 0.0),
            (17, 18),
        )
        np.testing.assert_allclose(direction, [0.0, -1.0, 0.0])

        with self.assertRaisesRegex(GeometryError, "顺序不连续"):
            _terminal_arch_direction(
                (18, 17, 16, 15),
                18,
                16,
                Vec3(0.0, 1.0, 0.0),
                (18, 17),
            )


if __name__ == "__main__":
    unittest.main()
