from __future__ import annotations

import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image as PillowImage

MEDIA_BYTES: dict[str, bytes | Exception] = {}


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


class FakeMediaResolver:
    def __init__(self, source: str, *, media_type: str) -> None:
        self.source = source
        self.media_type = media_type

    async def to_bytes(self) -> bytes:
        value = MEDIA_BYTES[self.source]
        if isinstance(value, Exception):
            raise value
        return value

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
    media_utils.MediaResolver = FakeMediaResolver

    async def extract_quoted_message_images(event: object) -> list[str]:
        return list(getattr(event, "quoted_images", []))

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
PROJECT_ROOT = Path(__file__).parents[1]


class FakeEvent:
    def __init__(self, text: str, *, admin: bool = False) -> None:
        self.message_str = text
        self._messages = [FakePlain(text)]
        self._admin = admin
        self.stopped = False
        self.quoted_images: list[str] = []

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


def png_bytes(color: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as image_file:
        path = Path(image_file.name)
    try:
        write_png(path, color)
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


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

    def test_draw_command_uses_configured_limit_in_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original = plugin_main.get_astrbot_data_path
            plugin_main.get_astrbot_data_path = lambda: directory
            try:
                plugin = plugin_main.Main(object(), {"max_draw_count": 8})
                result = run_handler(plugin, FakeEvent("抽 9 猫猫"))[0]
                self.assertEqual(
                    result.text,
                    "抽取数量必须在 1 到 8 之间。",
                )
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

    def test_random_commands_do_not_repeat_until_all_images_are_used(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")
        write_png(self.group_dir / "狗狗" / "one.png", "blue")
        write_png(self.group_dir / "狗狗" / "two.png", "green")

        selected_paths: list[str] = []
        with patch.object(plugin_main.random, "choice", side_effect=lambda items: items[0]):
            for _ in range(3):
                result = run_handler(self.plugin, FakeEvent("随机来点"))[0]
                selected_paths.append(
                    next(
                        item.file
                        for item in result.chain
                        if isinstance(item, FakeImage)
                    )
                )

        self.assertEqual(len(set(selected_paths)), 3)

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


class DeleteTests(unittest.TestCase):
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
        MEDIA_BYTES.clear()
        MEDIA_BYTES["quoted"] = png_bytes("red")

    def tearDown(self) -> None:
        plugin_main.get_astrbot_data_path = self.original_data_path
        self.temp_dir.cleanup()
        MEDIA_BYTES.clear()

    def add_same_image(self, gallery_name: str) -> Path:
        path = self.group_dir / gallery_name / "same.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(MEDIA_BYTES["quoted"])
        return path

    def test_admin_reply_delete_removes_only_named_gallery(self) -> None:
        cat_path = self.add_same_image("猫猫")
        dog_path = self.add_same_image("狗狗")
        event = FakeEvent("删除 猫猫", admin=True)
        event.quoted_images = ["quoted"]

        result = run_handler(self.plugin, event)[0]

        self.assertEqual(result.text, "已从“猫猫”图库删除这张图片。")
        self.assertFalse(cat_path.exists())
        self.assertTrue(dog_path.exists())

    def test_non_admin_reply_delete_changes_nothing(self) -> None:
        image_path = self.add_same_image("猫猫")
        event = FakeEvent("删除 猫猫")
        event.quoted_images = ["quoted"]

        result = run_handler(self.plugin, event)[0]

        self.assertEqual(result.text, "只有管理员可以删除图库图片。")
        self.assertNotIn("AstrBot", result.text)
        self.assertTrue(image_path.exists())

    def test_delete_without_reply_does_not_remove_gallery(self) -> None:
        image_path = self.add_same_image("猫猫")

        result = run_handler(
            self.plugin,
            FakeEvent("删除 猫猫", admin=True),
        )[0]

        self.assertEqual(
            result.text,
            "请回复一张图库图片并发送“删除 图库名”。",
        )
        self.assertTrue(image_path.exists())

    def test_reply_image_must_exist_in_named_gallery(self) -> None:
        image_path = self.group_dir / "猫猫" / "blue.png"
        write_png(image_path, "blue")
        event = FakeEvent("删除 猫猫", admin=True)
        event.quoted_images = ["quoted"]

        result = run_handler(self.plugin, event)[0]

        self.assertEqual(result.text, "“猫猫”图库中没有找到这张图片。")
        self.assertTrue(image_path.exists())

    def test_delete_rejects_invalid_gallery_name(self) -> None:
        event = FakeEvent("删除 ../猫猫", admin=True)
        event.quoted_images = ["quoted"]
        result = run_handler(self.plugin, event)[0]
        self.assertEqual(result.text, "图库名不合法。")

    def test_delete_reports_quoted_image_read_failure(self) -> None:
        MEDIA_BYTES["broken"] = RuntimeError("download failed")
        event = FakeEvent("删除 猫猫", admin=True)
        event.quoted_images = ["broken"]
        result = run_handler(self.plugin, event)[0]
        self.assertEqual(result.text, "读取被回复的图片失败，请稍后再试。")

    def test_clean_hash_command_still_removes_whole_gallery(self) -> None:
        image_path = self.add_same_image("猫猫")

        result = run_handler(
            self.plugin,
            FakeEvent("#清理 猫猫", admin=True),
        )[0]

        self.assertIn("已删除“猫猫”图库", result.text)
        self.assertFalse(image_path.parent.exists())

    def test_non_admin_clean_message_uses_generic_admin_wording(self) -> None:
        self.add_same_image("猫猫")
        result = run_handler(self.plugin, FakeEvent("#清理 猫猫"))[0]
        self.assertEqual(result.text, "只有管理员可以删除图库。")
        self.assertNotIn("AstrBot", result.text)


class DocumentationTests(unittest.TestCase):
    def test_readme_documents_current_command_contract(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`随机来点`、`随机来只`", readme)
        self.assertIn("`预览全部`", readme)
        self.assertIn("回复图片并发送 `删除 图库名`", readme)
        self.assertIn("`#清理 图库名`", readme)
        self.assertIn("一轮内不会重复", readme)
        self.assertNotIn("仅 AstrBot 管理员可用", readme)
        self.assertNotIn("`删除 猫猫` 或 `#清理 猫猫`", readme)


if __name__ == "__main__":
    unittest.main()
