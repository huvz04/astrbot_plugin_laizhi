from __future__ import annotations

import importlib
import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path

from PIL import Image as PillowImage


class FakePlain:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeImage:
    def __init__(self, file: str = "") -> None:
        self.file = file

    @classmethod
    def fromFileSystem(cls, file: str):
        return cls(file)


class FakeNode:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class FakeNodes:
    def __init__(self, nodes: list[FakeNode]) -> None:
        self.nodes = nodes


class FakeResult:
    def __init__(
        self,
        kind: str,
        *,
        text: str = "",
        chain: list[object] | None = None,
        image: str = "",
    ) -> None:
        self.kind = kind
        self.text = text
        self.chain = chain or []
        self.image = image

class FakeStar:
    def __init__(self, context: object, config: dict | None = None) -> None:
        self.context = context
        self.config = config or {}


def passthrough_decorator(*args: object, **kwargs: object):
    def decorate(value):
        return value

    return decorate


def install_astrbot_stubs() -> None:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    components = types.ModuleType("astrbot.api.message_components")
    event = types.ModuleType("astrbot.api.event")
    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")
    media_utils = types.ModuleType("astrbot.core.utils.media_utils")
    quoted_message = types.ModuleType("astrbot.core.utils.quoted_message")

    api.logger = types.SimpleNamespace(exception=lambda *args, **kwargs: None)
    api.star = types.SimpleNamespace(Star=FakeStar, Context=object)
    event.AstrMessageEvent = object
    event.filter = types.SimpleNamespace(
        EventMessageType=types.SimpleNamespace(GROUP_MESSAGE="group"),
        event_message_type=passthrough_decorator,
    )
    components.Image = FakeImage
    components.Plain = FakePlain
    components.Node = FakeNode
    components.Nodes = FakeNodes
    astrbot_path.get_astrbot_data_path = lambda: tempfile.gettempdir()
    media_utils.MediaResolver = type("MediaResolver", (), {})

    async def extract_quoted_message_images(event: object) -> list[str]:
        return []

    quoted_message.extract_quoted_message_images = extract_quoted_message_images
    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.message_components": components,
            "astrbot.api.event": event,
            "astrbot.core": core,
            "astrbot.core.utils": utils,
            "astrbot.core.utils.astrbot_path": astrbot_path,
            "astrbot.core.utils.media_utils": media_utils,
            "astrbot.core.utils.quoted_message": quoted_message,
        }
    )


install_astrbot_stubs()
plugin_main = importlib.import_module("main")


class FakeEvent:
    def __init__(self, text: str, *, admin: bool = False) -> None:
        self.message_str = text
        self._messages = [FakePlain(text)]
        self._admin = admin
        self.stopped = False

    def get_messages(self) -> list[object]:
        return self._messages

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    def get_group_id(self) -> str:
        return "100"

    def get_sender_id(self) -> str:
        return "200"

    def get_self_id(self) -> str:
        return "300"

    def is_admin(self) -> bool:
        return self._admin

    def stop_event(self) -> None:
        self.stopped = True

    def plain_result(self, text: str) -> FakeResult:
        return FakeResult("plain", text=text)

    def chain_result(self, chain: list[object]) -> FakeResult:
        return FakeResult("chain", chain=chain)

    def image_result(self, image: str) -> FakeResult:
        return FakeResult("image", image=image)


def run_handler(plugin: object, event: FakeEvent) -> list[FakeResult]:
    async def collect() -> list[FakeResult]:
        return [result async for result in plugin.on_group_message(event)]

    return asyncio.run(collect())


def write_png(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    PillowImage.new("RGB", (2, 2), color=color).save(path)


class ConfigTests(unittest.TestCase):
    def test_max_draw_count_uses_config_and_safe_default(self) -> None:
        cases = [
            ({}, 20),
            ({"max_draw_count": 8}, 8),
            ({"max_draw_count": "12"}, 12),
            ({"max_draw_count": 0}, 20),
            ({"max_draw_count": "bad"}, 20),
        ]
        with tempfile.TemporaryDirectory() as directory:
            original = plugin_main.get_astrbot_data_path
            plugin_main.get_astrbot_data_path = lambda: directory
            try:
                for config, expected in cases:
                    with self.subTest(config=config):
                        plugin = plugin_main.Main(object(), config)
                        self.assertEqual(plugin._max_draw_count(), expected)
            finally:
                plugin_main.get_astrbot_data_path = original


class BrowseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_path = plugin_main.get_astrbot_data_path
        plugin_main.get_astrbot_data_path = lambda: self.temp_dir.name
        self.plugin = plugin_main.Main(object(), {})
        self.group_dir = (
            Path(self.temp_dir.name)
            / "plugin_data"
            / plugin_main.PLUGIN_NAME
            / "aiocqhttp"
            / "100"
        )

    def tearDown(self) -> None:
        plugin_main.get_astrbot_data_path = self.original_data_path
        self.temp_dir.cleanup()

    def test_random_commands_return_image_with_source_gallery(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")
        write_png(self.group_dir / "狗狗" / "one.png", "blue")

        for command in ("随机来点", "随机来只"):
            with self.subTest(command=command):
                result = run_handler(self.plugin, FakeEvent(command))[0]
                self.assertEqual(result.kind, "chain")
                self.assertTrue(
                    any(isinstance(item, FakeImage) for item in result.chain)
                )
                source = next(
                    item.text
                    for item in result.chain
                    if isinstance(item, FakePlain)
                )
                self.assertIn(
                    source,
                    {"🎲 随机来自图库「猫猫」", "🎲 随机来自图库「狗狗」"},
                )

    def test_random_commands_require_full_match(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")
        for text in ("帮我随机来点", "随机来只猫猫"):
            with self.subTest(text=text):
                self.assertEqual(run_handler(self.plugin, FakeEvent(text)), [])

    def test_preview_all_lists_sorted_non_empty_galleries(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")
        write_png(self.group_dir / "猫猫" / "two.png", "green")
        write_png(self.group_dir / "狗狗" / "one.png", "blue")
        (self.group_dir / "空库").mkdir(parents=True)

        result = run_handler(self.plugin, FakeEvent("预览全部"))[0]

        self.assertEqual(result.kind, "plain")
        self.assertEqual(
            result.text,
            "当前群图库：\n狗狗：1 张\n猫猫：2 张",
        )

    def test_preview_all_reports_empty_state(self) -> None:
        result = run_handler(self.plugin, FakeEvent("预览全部"))[0]
        self.assertEqual(result.text, "当前群还没有可用的图库。")

    def test_ordinary_get_remains_image_only(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")
        result = run_handler(self.plugin, FakeEvent("来点猫猫"))[0]
        self.assertEqual(result.kind, "image")


if __name__ == "__main__":
    unittest.main()
