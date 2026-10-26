from __future__ import annotations

import unittest

from stats_charts import wordcloud_layout


class WordcloudTests(unittest.TestCase):
    def test_all_labels_fit_without_overlap_at_configured_maximum(self):
        for length in (2, 30):
            items = [{"name": "猫" * (length - 2) + f"{i:02d}", "count": 201 - i} for i in range(200)]
            words = wordcloud_layout(items)
            self.assertEqual(len(words), len(items))
            for i, word in enumerate(words):
                self.assertGreater(word["size"], 0)
                self.assertGreaterEqual(word["x"], 0)
                self.assertLessEqual(word["x"] + word["width"], 1000)
                self.assertGreaterEqual(word["y"] - word["size"] / 2, 0)
                self.assertLessEqual(word["y"] + word["size"] / 2, 570)
                for other in words[:i]:
                    overlaps = (
                        word["x"] < other["x"] + other["width"]
                        and other["x"] < word["x"] + word["width"]
                        and abs(word["y"] - other["y"]) < (word["size"] + other["size"]) / 2
                    )
                    self.assertFalse(overlaps)


if __name__ == "__main__":
    unittest.main()
