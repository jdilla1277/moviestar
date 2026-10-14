import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class FrameContractTest(unittest.TestCase):
    def test_fresh_measurement_drives_preview_and_export_on_two_canvases(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "frame_contract_probe.py")],
            check=True, capture_output=True, text=True,
        )
        cases = json.loads(result.stdout)["cases"]
        self.assertEqual(len(cases), 2)
        self.assertEqual([case["canvas"] for case in cases], ["1280x720", "720x1280"])
        self.assertEqual([case["sample_time_s"] for case in cases], [1.25, 4.5])
        self.assertEqual([case["source_time_s"] for case in cases], [2.25, 5.5])
        self.assertGreaterEqual(cases[1]["measured_box"]["x"] - cases[0]["measured_box"]["x"], 30)
        for case in cases:
            self.assertLessEqual(case["browser_edge_error_px"], 1)
            self.assertLessEqual(case["preview_edge_error_px"], 1)
            self.assertLessEqual(case["export_edge_error_px"], 1)
            self.assertEqual(case["description_used_for_preview"], case["description_used_for_export"])


if __name__ == "__main__":
    unittest.main()
