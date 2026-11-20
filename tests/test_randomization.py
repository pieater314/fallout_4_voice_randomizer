import unittest

from voice_randomizer.randomization import shuffle_pool


class ShufflePoolTests(unittest.TestCase):
    def test_derangement_is_one_to_one(self):
        paths = [f"line{i}.fuz" for i in range(10)]
        result = shuffle_pool(paths, 100)

        self.assertEqual(set(result), set(paths))
        self.assertEqual(set(result.values()), set(paths))
        self.assertTrue(all(destination != source for destination, source in result.items()))

    def test_repeatable_when_input_order_changes(self):
        paths = ["A.fuz", "B.fuz", "C.fuz", "D.fuz"]
        self.assertEqual(shuffle_pool(paths, "seed"), shuffle_pool(reversed(paths), "seed"))

    def test_small_pools(self):
        self.assertEqual(shuffle_pool([], 1), {})
        self.assertEqual(shuffle_pool(["only.fuz"], 1), {"only.fuz": "only.fuz"})
        self.assertEqual(
            shuffle_pool(["a.fuz", "b.fuz"], 1),
            {"a.fuz": "b.fuz", "b.fuz": "a.fuz"},
        )

    def test_duplicate_windows_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            shuffle_pool(["voice\\Line.fuz", "VOICE/line.FUZ"], 1)

    def test_empty_path_is_rejected(self):
        with self.assertRaises(ValueError):
            shuffle_pool([""], 1)


if __name__ == "__main__":
    unittest.main()
