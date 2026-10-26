"""Render sample HTML with the production template for visual inspection."""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import sys
import tempfile
from pathlib import Path

from jinja2 import Environment
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import test_main  # noqa: E402


async def preview(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    labels = "猫猫 狗狗 小鸟 兔兔 仓鼠 柴犬 水獭 熊猫 海豹 狐狸 咖啡 蛋糕 拉面 晚安 早安 开心 发呆 可爱 加油 委屈 梅花 樱花 夕阳 星空 蓝天 大海 音乐 游戏 旅行 日常".split()
    galleries = [{"name": label, "count": int(600 / (i + 1) ** 0.7)} for i, label in enumerate(labels)]
    avatar = Image.new("RGB", (100, 100), "#c0ddd0")
    draw = ImageDraw.Draw(avatar)
    draw.ellipse((30, 16, 70, 56), fill="#608b79")
    draw.ellipse((12, 58, 88, 130), fill="#608b79")
    buffer = io.BytesIO()
    avatar.save(buffer, format="PNG")
    source = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    with tempfile.TemporaryDirectory() as directory:
        test_main.plugin_main.get_astrbot_data_path = lambda: directory
        plugin = test_main.plugin_main.Main(object(), {})
        plugin._avatar_data = lambda user_id: "" if user_id == "2" else source

        async def render(template: str, data: dict, **kwargs) -> str:
            path = output / f"{data['kind']}.html"
            path.write_text(Environment(autoescape=True).from_string(template).render(**data), encoding="utf-8")
            print(path)
            return str(path)

        plugin.html_render = render
        data = {
            "galleries": galleries, "gallery_count": len(galleries),
            "image_count": sum(item["count"] for item in galleries),
            "additions": 2834, "recent_days": 7, "recent_calls": 728,
            "popular": [{"gallery_name": row["name"], "count": row["count"] // 2 + 1} for row in galleries[::2]],
            "contributors": [{"user_id": str(i), "name": name, "count": max(1, 980 - i * 93)} for i, name in enumerate(("小梅", "花见", "猫猫饲养员", "这是一个需要省略显示的非常非常长的上传者名字用于布局测试", "小樱", "Cloud", "今天也要开心", "阿夏", "星星", "晚风"))],
        }
        data["additions"] = sum(row["count"] for row in data["contributors"])
        data["recent_calls"] = sum(row["count"] for row in data["popular"])
        for kind in ("keywords", "contributors", "popular"):
            await plugin._render_overview(data, kind, "aiocqhttp")
        empty = {**data, "galleries": [], "contributors": [], "popular": [], "additions": 0, "recent_calls": 0, "gallery_count": 0, "image_count": 0}
        output = output / "empty"
        output.mkdir(exist_ok=True)
        for kind in ("keywords", "contributors", "popular"):
            await plugin._render_overview(empty, kind, "aiocqhttp")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    asyncio.run(preview(parser.parse_args().output))
