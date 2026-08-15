import unittest

from twin_guide.guide_post_positioning import (
    DRILL_HANDPIECE_INSERTION_MM,
    DRILL_LENGTH_INSIDE_HANDPIECE_MM,
    calculate_twin_guide_extension_mm,
)


class TwinGuideExtensionTests(unittest.TestCase):
    def test_uses_fixed_twelve_millimeter_length_inside_handpiece(self):
        self.assertEqual(DRILL_LENGTH_INSIDE_HANDPIECE_MM, 12.0)
        self.assertEqual(DRILL_HANDPIECE_INSERTION_MM, 12.0)
        self.assertEqual(calculate_twin_guide_extension_mm(35.0, 11.0), 12.0)

    def test_preserves_decimal_precision(self):
        self.assertAlmostEqual(
            calculate_twin_guide_extension_mm(34.75, 10.125),
            12.625,
        )

    def test_rejects_zero_extension(self):
        with self.assertRaisesRegex(ValueError, "延长量必须大于 0"):
            calculate_twin_guide_extension_mm(22.0, 10.0)

    def test_rejects_negative_extension(self):
        with self.assertRaisesRegex(ValueError, "延长量必须大于 0"):
            calculate_twin_guide_extension_mm(21.0, 10.0)

    def test_rejects_nonpositive_drill_length(self):
        for drill_length_mm in (0.0, -1.0):
            with (
                self.subTest(drill_length_mm=drill_length_mm),
                self.assertRaisesRegex(ValueError, "钻针长度必须大于 0"),
            ):
                calculate_twin_guide_extension_mm(drill_length_mm, 10.0)

    def test_rejects_nonpositive_implant_length(self):
        for implant_length_mm in (0.0, -1.0):
            with (
                self.subTest(implant_length_mm=implant_length_mm),
                self.assertRaisesRegex(ValueError, "植体长度必须大于 0"),
            ):
                calculate_twin_guide_extension_mm(35.0, implant_length_mm)


if __name__ == "__main__":
    unittest.main()
