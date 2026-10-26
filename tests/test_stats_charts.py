from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from PIL import Image

from stats_charts import split_statistics_image, wordcloud_layout


class WordcloudTests(unittest.TestCase):
    def test_split_crops_all_panels_even_with_a_720_pixel_minimum_viewport(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("png", "jpg"):
                image = Image.new("RGB", (800, 720), (255, 253, 248))
                for index in range(4):
                    image.paste((255, 0, 255), (0, index * 200, 800, index * 200 + 4))
                    if index < 3:
                        image.paste((20, 40, 60), (20, index * 200 + 20, 700, index * 200 + 60))
                source = Path(directory) / f"batch.{extension}"
                image.save(source)
                output = split_statistics_image(source)
                self.assertEqual(len(output), 3)
                for path in output:
                    with Image.open(path) as panel:
                        self.assertEqual(panel.width, 800)
                        self.assertLess(panel.height, 70)

    def test_incomplete_batch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cutoff.png"
            Image.new("RGB", (800, 720), "white").save(source)
            with self.assertRaises(ValueError):
                split_statistics_image(source)

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
