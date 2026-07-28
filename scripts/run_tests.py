"""在 Blender 后台运行网格后端与端到端测试。"""

from __future__ import annotations

import unittest

from tests.blender.test_backend import BlenderBackendTests
from tests.end_to_end.test_case import EndToEndTests

if __name__ == "__main__":
    suite = unittest.TestSuite(
        (
            unittest.defaultTestLoader.loadTestsFromTestCase(BlenderBackendTests),
            unittest.defaultTestLoader.loadTestsFromTestCase(EndToEndTests),
        )
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
