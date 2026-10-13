import unittest

from model_spike import DEFINITIONS, podcast


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


if __name__ == "__main__":
    unittest.main()
