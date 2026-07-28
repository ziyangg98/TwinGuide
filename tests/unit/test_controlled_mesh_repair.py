"""受控网格修复的数值边界测试。"""

from __future__ import annotations

import unittest
import warnings

import numpy as np
import trimesh

from twin_guide.tooth_mapping.controlled_mesh_repair import _topology_summary


class ControlledMeshRepairTests(unittest.TestCase):
    """验证病态输入的拓扑摘要不会产生浮点警告。"""

    def test_zero_volume_triangle_is_reported_without_runtime_warning(self) -> None:
        """单三角形应被标记为非实体，而不是在质心计算中除零。"""

        mesh = trimesh.Trimesh(
            vertices=np.asarray(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))),
            faces=np.asarray(((0, 1, 2),)),
            process=False,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            summary = _topology_summary(mesh)

        self.assertFalse(summary["is_closed_volume"])
        self.assertEqual(summary["signed_volume_mm3"], 0.0)


if __name__ == "__main__":
    unittest.main()
