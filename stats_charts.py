from __future__ import annotations

import math
import unicodedata
from pathlib import Path

from PIL import Image, ImageChops

CHART_KINDS = ("keywords", "contributors", "popular")


def split_statistics_image(source: Path) -> list[Path]:
    with Image.open(source) as original:
        image = original.convert("RGB")
    samples = (0, image.width // 4, image.width // 2, image.width * 3 // 4, image.width - 1)
    pixels = image.load()
    separators: list[list[int]] = []
    for y in range(image.height):
        if all(pixels[x, y][0] > 200 and pixels[x, y][1] < 100 and pixels[x, y][2] > 180 for x in samples):
            if separators and separators[-1][1] == y:
                separators[-1][1] = y + 1
            else:
                separators.append([y, y + 1])
    if len(separators) != 4:
        raise ValueError("统计图渲染不完整，无法分成三张图片。")
    output = []
    for index, kind in enumerate(CHART_KINDS):
        panel = image.crop((0, separators[index][1] + 2, image.width, separators[index + 1][0] - 2))
        background = Image.new("RGB", panel.size, (255, 253, 248))
        mask = ImageChops.difference(panel, background).convert("L").point(lambda value: 255 if value > 18 else 0)
        bounds = mask.getbbox()
        if bounds is None:
            raise ValueError(f"统计图 {kind} 没有可见内容。")
        padding = max(8, image.width // 100)
        panel = panel.crop((0, max(0, bounds[1] - padding), panel.width, min(panel.height, bounds[3] + padding)))
        path = source.with_name(f"{source.stem}-{kind}.png")
        panel.save(path, format="PNG")
        output.append(path)
    return output


def wordcloud_layout(items: list[dict], *, width: int = 1000, height: int = 570) -> list[dict]:
    if not items:
        return []
    maximum = max(item["count"] for item in items)
    colors = ("#155e58", "#206b8c", "#b55426", "#665a96", "#6d7940", "#ad526b")
    for attempt in range(30):
        scale = 0.89 ** attempt
        occupied = [0] * math.ceil(height / 8)
        placed = []
        for rank, item in enumerate(items):
            label = f"{item['name']}({item['count']})"
            units = sum(1 if unicodedata.east_asian_width(c) in "WF" else 0.6 for c in label)
            frequency = item["count"] / maximum
            size = min(12 + 108 * frequency ** 1.5, (width - 40) / units) * scale
            text_width = units * size
            box_width, box_height = text_width + 16, size * 1.35 + 12
            for step in range(4000):
                angle = step * 0.32 + rank * 0.7
                radius = 3.4 * math.sqrt(step)
                x = width / 2 + math.cos(angle) * radius * 2.5 - box_width / 2
                y = height / 2 + math.sin(angle) * radius * 1.5 - box_height / 2
                if x < 0 or y < 0 or x + box_width > width or y + box_height > height:
                    continue
                left = int(x // 8)
                mask = ((1 << (math.ceil((x + box_width) / 8) - left)) - 1) << left
                rows = range(int(y // 8), math.ceil((y + box_height) / 8))
                if not any(occupied[row] & mask for row in rows):
                    for row in rows:
                        occupied[row] |= mask
                    placed.append({
                        **item, "label": label, "x": round(x + 8, 2),
                        "y": round(y + box_height / 2, 2),
                        "size": round(size, 2), "width": round(text_width, 2),
                        "color": colors[rank % len(colors)],
                        "weight": 750 if frequency >= 0.6 else 600 if frequency >= 0.2 else 500,
                    })
                    break
            else:
                break
        if len(placed) == len(items):
            return placed
    raise ValueError("词云关键词过多，无法排入图片")
