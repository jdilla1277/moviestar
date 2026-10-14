import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class LayerOrderTest(unittest.TestCase):
    def test_parent_background_and_foreground_graphics_preserve_video(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "layer_order_probe.py")],
            check=True, capture_output=True, text=True,
        )
        metrics = json.loads(result.stdout)
        self.assertGreater(metrics["flat_inside_video_error"], 50)
        self.assertLessEqual(metrics["ordered_inside_video_error"], 8)
        self.assertLessEqual(metrics["ordered_card_background_error"], 8)
        self.assertLessEqual(metrics["ordered_foreground_error"], 8)
        self.assertLessEqual(metrics["ordered_outside_clip_error"], 8)


if __name__ == "__main__":
    unittest.main()
