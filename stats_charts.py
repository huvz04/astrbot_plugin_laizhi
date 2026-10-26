from __future__ import annotations

import math
import unicodedata


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
