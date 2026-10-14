"""The renderer gate checks content, timing, and complete-path budgets."""

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class TemporalGateTest(unittest.TestCase):
    def test_source_frames_captions_transitions_and_budgets(self):
        run = subprocess.run(
            [sys.executable, str(ROOT / "temporal_gate.py"), "--verify"],
            capture_output=True, text=True, check=True,
        )
        result = json.loads(run.stdout)
        self.assertEqual(result["problems"], [])
        self.assertEqual([case["canvas"] for case in result["cases"]],
                         ["1280x720", "720x1280"])
        for case in result["cases"]:
            self.assertLessEqual(case["max_source_frame_error"], 1)
            self.assertLessEqual(case["max_transition_color_error"], 20)
            self.assertLessEqual(case["max_geometry_error_px"], 1)
            self.assertLessEqual(case["max_caption_pixel_error"], 20)
            self.assertLessEqual(case["max_caption_glyph_bound_error_px"], 2)
            self.assertLessEqual(case["max_transition_barcode_pixel_error"], 20)
            self.assertLessEqual(case["preview_export_barcode_pixel_error"], 20)
            self.assertLessEqual(case["preview_export_caption_glyph_bound_error_px"], 2)
            self.assertEqual(case["caption_line_counts_at_boundary"], [1, 2, 2])
            self.assertEqual(case["export_caption_line_counts"], [1, 2, 2])
            self.assertEqual(case["caption_box_delta_px"], 0)
        self.assertLessEqual(result["performance"]["cold_preview_p95_ms"], 3000)
        self.assertLessEqual(result["performance"]["warm_preview_p95_ms"], 700)
        self.assertLessEqual(result["performance"]["export_end_to_end_ms"], 6000)
        self.assertLessEqual(result["performance"]["peak_rss_mib"], 2048)
        self.assertTrue(result["versions"]["chrome"])
        self.assertTrue(result["versions"]["ffmpeg"])


if __name__ == "__main__":
    unittest.main()
