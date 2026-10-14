import unittest

from model_spike import DEFINITIONS, expand, podcast


class ComponentExpansionTest(unittest.TestCase):
    def test_builtin_and_custom_components_share_expansion_path(self):
        self.assertEqual(DEFINITIONS["Stack"]["origin"], "built_in")
        self.assertEqual(DEFINITIONS["ListenerPair"]["origin"], "custom")
        first = podcast("alice", "bob")
        second = podcast("bob", "alice")
        self.assertEqual(first.count('data-node="listener-pair"'), 1)
        self.assertEqual(second.count('data-node="listener-pair"'), 1)
        self.assertLess(first.index("alice.mp4"), first.index("bob.mp4"))
        self.assertLess(second.index("bob.mp4"), second.index("alice.mp4"))
        self.assertIn("ListenerPair/Stack/listener-pair", first)

    def test_video_card_tracks_its_video_occurrence(self):
        card = expand({"component": "VideoCard", "props": {
            "source": "layer_order_red", "badge": "ON TOP",
        }})
        self.assertIn('data-path="VideoCard/card/video"', card)
        self.assertIn('layer_order_red.mp4', card)
        self.assertIn('ON TOP', card)


if __name__ == "__main__":
    unittest.main()
