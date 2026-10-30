from __future__ import annotations

import asyncio
import base64
import importlib
import io
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
    def __init__(self, file: str = "", data: bytes | None = None) -> None:
        self.file = file
        self.data = data

    @classmethod
    def fromFileSystem(cls, file: str):
        return cls(file)

    async def convert_to_base64(self) -> str:
        return base64.b64encode(self.data or b"").decode("ascii")


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
        self.last_render: tuple[str, dict, dict | None] | None = None
        self.renders: list[dict] = []

    async def html_render(
        self,
        template: str,
        data: dict,
        return_url: bool = False,
        options: dict | None = None,
    ) -> str:
        self.last_render = (template, data, options)
        self.renders.extend(data["charts"])
        width = options["viewport_width"]
        image = PillowImage.new("RGB", (width, 316), (255, 253, 248))
        for index in range(4):
            image.paste((255, 0, 255), (0, index * 104, width, index * 104 + 4))
            if index < 3:
                image.paste((20, 40, 60), (10, index * 104 + 24, 100, index * 104 + 70))
        path = self.data_dir / "mock-statistics.png"
        image.save(path)
        return str(path)


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
        self.sent: list[FakeResult] = []

    def get_messages(self) -> list[object]:
        return self._messages

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    def get_group_id(self) -> str:
        return "100"

    def get_sender_id(self) -> str:
        return "200"

    def get_sender_name(self) -> str:
        return "测试用户"

    def get_self_id(self) -> str:
        return "300"

    def is_admin(self) -> bool:
        return self._admin

    def stop_event(self) -> None:
        self.stopped = True

    async def send(self, result: FakeResult) -> None:
        self.sent.append(result)

    def plain_result(self, text: str) -> FakeResult:
        return FakeResult("plain", text=text)

    def chain_result(self, chain: list[object]) -> FakeResult:
        return FakeResult("chain", chain=chain)

    def image_result(self, image: str) -> FakeResult:
        return FakeResult("image", image=image)


def run_handler(plugin: object, event: FakeEvent) -> list[FakeResult]:
    async def collect() -> list[FakeResult]:
        yielded = [result async for result in plugin.on_group_message(event)]
        return event.sent + yielded

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

    def test_random_commands_do_not_repeat_inside_selected_gallery(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")
        write_png(self.group_dir / "猫猫" / "two.png", "green")
        write_png(self.group_dir / "狗狗" / "one.png", "blue")

        selected_paths: list[str] = []
        with (
            patch.object(
                plugin_main.random,
                "choices",
                side_effect=lambda items, **kwargs: [items[-1]],
            ),
            patch.object(plugin_main.random, "choice", side_effect=lambda items: items[0]),
        ):
            for _ in range(2):
                result = run_handler(self.plugin, FakeEvent("随机来点"))[0]
                selected_paths.append(
                    next(
                        item.file
                        for item in result.chain
                        if isinstance(item, FakeImage)
                    )
                )

        self.assertEqual(len(set(selected_paths)), 2)

    def test_admin_can_set_persistent_gallery_weight(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")

        denied = run_handler(self.plugin, FakeEvent("权重 猫猫 0.5"))[0]
        self.assertEqual(denied.text, "只有管理员可以设置图库权重。")

        result = run_handler(
            self.plugin,
            FakeEvent("权重 猫猫 0.5", admin=True),
        )[0]
        self.assertEqual(result.text, "已将“猫猫”图库的随机权重设为 0.5。")
        self.assertEqual(
            (self.group_dir / "猫猫" / ".weight").read_text(encoding="utf-8"),
            "0.5",
        )
        self.assertEqual(
            self.plugin._gallery_weight("aiocqhttp", "100", "猫猫"),
            0.5,
        )

    def test_random_command_uses_gallery_weights_and_skips_zero(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")
        write_png(self.group_dir / "狗狗" / "one.png", "blue")
        (self.group_dir / "猫猫" / ".weight").write_text("0", encoding="utf-8")

        with patch.object(
            plugin_main.random,
            "choices",
            side_effect=lambda items, **kwargs: [items[0]],
        ) as choices:
            result = run_handler(self.plugin, FakeEvent("随机来点"))[0]

        self.assertEqual(choices.call_args.kwargs["weights"], [1.0])
        source = next(item.text for item in result.chain if isinstance(item, FakePlain))
        self.assertEqual(source, "🎲 随机来自图库「狗狗」")

    def test_random_commands_require_full_match(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")
        for text in ("帮我随机来点", "随机来只猫猫"):
            with self.subTest(text=text):
                self.assertEqual(run_handler(self.plugin, FakeEvent(text)), [])

    def test_statistics_sends_three_charts_with_inventory_and_recent_counts(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")
        write_png(self.group_dir / "猫猫" / "two.png", "green")
        write_png(self.group_dir / "狗狗" / "one.png", "blue")
        (self.group_dir / "空库").mkdir(parents=True)

        self.plugin.stats_store.record("GALLERY_CALLED", "aiocqhttp", "100", "狗狗")
        event = FakeEvent("来点统计")
        results = run_handler(self.plugin, event)

        self.assertTrue(event.stopped)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].kind, "chain")
        nodes = results[0].chain[0].nodes
        self.assertEqual(len(nodes), 3)
        self.assertEqual([Path(node.content[0].file).name for node in nodes], ["mock-statistics-keywords.png", "mock-statistics-contributors.png", "mock-statistics-popular.png"])
        self.assertEqual(self.plugin.last_render[2]["viewport_width"], 1200)
        self.assertEqual(self.plugin.last_render[2]["viewport_height"], 100)
        data = self.plugin.renders[0]
        self.assertEqual(
            data["galleries"],
            [{"name": "猫猫", "count": 2}, {"name": "狗狗", "count": 1}],
        )
        self.assertEqual({word["label"] for word in data["words"]}, {"猫猫(2)", "狗狗(1)"})
        self.assertEqual(self.plugin.renders[2]["words"][0]["label"], "狗狗(1)")

    def test_batch_render_failure_falls_back_to_text(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")

        async def fail_render(template: str, data: dict, **kwargs: object) -> str:
            raise RuntimeError("renderer unavailable")

        self.plugin.html_render = fail_render
        results = run_handler(self.plugin, FakeEvent("来只 统计"))
        self.assertEqual([result.kind for result in results], ["plain"])
        self.assertIn("统计图片生成失败", results[0].text)
        self.assertIn("猫猫  1 张", results[0].text)

    def test_merged_send_finishes_before_scheduler_can_observe_stop(self) -> None:
        event = FakeEvent("来只统计")

        async def first_yield():
            return await anext(self.plugin.on_group_message(event), None)

        self.assertIsNone(asyncio.run(first_yield()))
        self.assertTrue(event.stopped)
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(len(event.sent[0].chain[0].nodes), 3)

    def test_all_statistics_aliases_use_one_render_and_one_merged_send(self) -> None:
        original_render = self.plugin.html_render
        calls = []

        async def count_render(template: str, data: dict, **kwargs: object) -> str:
            calls.append(data)
            return await original_render(template, data, **kwargs)

        self.plugin.html_render = count_render
        for command in ("来点统计", "来点 统计", "来只统计", "来只 统计"):
            calls.clear()
            event = FakeEvent(command)
            run_handler(self.plugin, event)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(event.sent), 1)
            self.assertEqual(len(event.sent[0].chain[0].nodes), 3)

    def test_reserved_keyword_cannot_be_added_or_randomly_drawn(self) -> None:
        write_png(self.group_dir / "统计" / "old.png", "red")
        result = run_handler(self.plugin, FakeEvent("添加 统计"))[0]
        self.assertIn("保留词", result.text)
        self.assertEqual(self.plugin.pending_additions, {})
        self.assertIsNone(self.plugin._random_gallery_image("aiocqhttp", "100"))
        data = asyncio.run(self.plugin._overview_data("aiocqhttp", "100"))
        self.assertEqual(data["image_count"], 0)
        self.assertTrue((self.group_dir / "统计" / "old.png").exists())

    def test_statistics_requires_full_command_and_replaces_old_aliases(self) -> None:
        for text in ("预览全部", "图库统计", "帮我来只统计", "来只统计一下"):
            with self.subTest(text=text):
                self.plugin.renders.clear()
                run_handler(self.plugin, FakeEvent(text))
                self.assertEqual(self.plugin.renders, [])

    def test_avatar_download_is_embedded_and_cached_with_failure_fallback(self) -> None:
        with patch.object(plugin_main, "urlopen", return_value=io.BytesIO(png_bytes("blue"))) as fetch:
            first = self.plugin._avatar_data("200")
            cached = self.plugin._avatar_data("200")
        self.assertTrue(first.startswith("data:image/png;base64,"))
        self.assertEqual(first, cached)
        self.assertEqual(fetch.call_count, 1)
        with patch.object(plugin_main, "urlopen", side_effect=OSError("offline")):
            self.assertEqual(self.plugin._avatar_data("201"), "")
            self.assertEqual(self.plugin._avatar_data("../200"), "")

    def test_contribution_chart_uses_avatar_name_and_upload_count(self) -> None:
        self.plugin.stats_store.record("IMAGE_ADDED", "aiocqhttp", "100", "猫猫", user_id="200", user_name="上传者")
        with patch.object(self.plugin, "_avatar_data", return_value="data:image/png;base64,test"):
            run_handler(self.plugin, FakeEvent("来只统计"))
        row = self.plugin.renders[1]["contributors"][0]
        self.assertEqual((row["name"], row["count"], row["avatar"]), ("上传者", 1, "data:image/png;base64,test"))

    def test_ordinary_get_remains_image_only(self) -> None:
        write_png(self.group_dir / "猫猫" / "one.png", "red")
        result = run_handler(self.plugin, FakeEvent("来点猫猫"))[0]
        self.assertEqual(result.kind, "image")
        stats = self.plugin.stats_store.overview(
            "aiocqhttp", "100", recent_days=7, top_users=10
        )
        self.assertEqual(stats["recent_calls"], 1)
        self.assertEqual(stats["popular"][0]["gallery_name"], "猫猫")

    def test_duplicate_addition_counts_only_the_successful_new_image(self) -> None:
        payload = png_bytes("red")
        for _ in range(2):
            event = FakeEvent("添加 猫猫")
            event._messages.append(FakeImage(data=payload))
            run_handler(self.plugin, event)

        stats = self.plugin.stats_store.overview(
            "aiocqhttp", "100", recent_days=7, top_users=10
        )
        self.assertEqual(stats["additions"], 1)
        self.assertEqual(stats["contributors"][0]["name"], "测试用户")

    def test_image_info_reports_qq_and_recorded_time(self) -> None:
        payload = png_bytes("red")
        digest = plugin_main.hashlib.md5(payload).hexdigest()
        path = self.group_dir / "猫猫" / "legacy-name.png"
        write_png(path, "red")
        self.plugin.stats_store.record(
            "IMAGE_ADDED", "aiocqhttp", "100", "猫猫", image_md5=digest,
            user_id="123456", user_name="原添加者",
            created_at=plugin_main.datetime(2026, 10, 30, 8, 30, tzinfo=plugin_main.timezone.utc),
        )
        event = FakeEvent("图片信息")
        event._messages.append(FakeImage(data=payload))
        result = run_handler(self.plugin, event)[0]
        self.assertIn("图库：猫猫", result.text)
        self.assertIn("添加人QQ：123456（原添加者）", result.text)
        self.assertIn("2026-10-30 16:30:00（北京时间）", result.text)
        self.assertIn(digest, result.text)

    def test_imported_image_provenance_is_unknown_and_other_groups_are_private(self) -> None:
        payload = png_bytes("red")
        digest = plugin_main.hashlib.md5(payload).hexdigest()
        write_png(self.group_dir / "猫猫" / "imported.png", "red")
        self.plugin.stats_store.record("IMAGE_ADDED", "aiocqhttp", "other", "猫猫", image_md5=digest, user_id="999999")
        event = FakeEvent("查图 猫猫")
        event._messages.append(FakeImage(data=payload))
        result = run_handler(self.plugin, event)[0]
        self.assertIn("添加人QQ：未知", result.text)
        self.assertIn("标记时间：未知", result.text)
        self.assertNotIn("999999", result.text)

    def test_reply_image_query_and_unknown_image(self) -> None:
        MEDIA_BYTES["info-reply"] = png_bytes("blue")
        event = FakeEvent("图片信息")
        event.quoted_images = ["info-reply"]
        result = run_handler(self.plugin, event)[0]
        self.assertIn("本群未收录", result.text)
        self.assertIn("添加人QQ：未知", result.text)
        self.assertIn("标记时间：未知", result.text)
        self.assertIn("请回复或附带", run_handler(self.plugin, FakeEvent("图片信息"))[0].text)

    def test_explicit_add_cancels_old_pending_upload(self) -> None:
        run_handler(self.plugin, FakeEvent("添加 猫猫"))
        self.assertTrue(self.plugin.pending_additions)
        event = FakeEvent("添加 狗狗")
        event._messages.append(FakeImage(data=png_bytes("blue")))
        run_handler(self.plugin, event)
        self.assertFalse(self.plugin.pending_additions)
        next_image = FakeEvent("")
        next_image._messages.append(FakeImage(data=png_bytes("red")))
        self.assertEqual(run_handler(self.plugin, next_image), [])
        self.assertFalse((self.group_dir / "猫猫").exists())

    def test_overwritten_file_does_not_produce_false_duplicate_or_destroy_other_image(self) -> None:
        red = png_bytes("red")
        blue = png_bytes("blue")

        async def exercise():
            path, _ = await self.plugin._store_image(FakeImage(data=red), "aiocqhttp", "100", "猫猫")
            path.write_bytes(blue)
            repaired, is_new = await self.plugin._store_image(FakeImage(data=red), "aiocqhttp", "100", "猫猫")
            return repaired, is_new

        path, is_new = asyncio.run(exercise())
        self.assertTrue(is_new)
        self.assertEqual(path.read_bytes(), red)
        self.assertEqual({image.read_bytes() for image in self.plugin._gallery_images("aiocqhttp", "100", "猫猫")}, {red, blue})

    def test_invalid_and_duplicate_files_are_quarantined_but_healthy_files_survive(self) -> None:
        folder = self.group_dir / "猫猫"
        write_png(folder / "first.png", "red")
        write_png(folder / "second.png", "red")
        write_png(folder / "valid.png", "blue")
        (folder / "broken.png").write_bytes(b"broken")
        (folder / "empty.jpg").write_bytes(b"")
        (folder / ".weight").write_text("0.5")
        images = self.plugin._gallery_images("aiocqhttp", "100", "猫猫")
        self.assertEqual(len(images), 2)
        quarantined = list((self.plugin.data_dir / "_quarantine" / "aiocqhttp" / "100" / "猫猫").iterdir())
        self.assertEqual(len(quarantined), 3)
        self.assertEqual((folder / ".weight").read_text(), "0.5")
        self.assertEqual(self.plugin._gallery_weight("aiocqhttp", "100", "猫猫"), 0.5)
        self.assertEqual(len(self.plugin._gallery_images("aiocqhttp", "100", "猫猫")), 2)

    def test_corrupt_canonical_file_is_replaced_by_a_real_new_upload(self) -> None:
        payload = png_bytes("red")
        digest = plugin_main.hashlib.md5(payload).hexdigest()
        write_png(self.group_dir / "猫猫" / f"{digest}.png", "red")
        self.plugin._gallery_images("aiocqhttp", "100", "猫猫")
        (self.group_dir / "猫猫" / f"{digest}.png").write_bytes(b"")
        event = FakeEvent("添加 猫猫")
        event._messages.append(FakeImage(data=payload))
        result = run_handler(self.plugin, event)[0]
        self.assertEqual(result.kind, "chain")
        self.assertEqual((self.group_dir / "猫猫" / f"{digest}.png").read_bytes(), payload)
        self.assertEqual(self.plugin.stats_store.image_record("aiocqhttp", "100", "猫猫", digest)["user_id"], "200")

    def test_concurrent_identical_uploads_are_stored_and_recorded_once(self) -> None:
        payload = png_bytes("red")

        async def exercise():
            return await asyncio.gather(*(
                self.plugin._store_image(FakeImage(data=payload), "aiocqhttp", "100", "猫猫", user_id="200")
                for _ in range(3)
            ))

        results = asyncio.run(exercise())
        self.assertEqual(sum(is_new for _, is_new in results), 1)
        self.assertEqual(len(self.plugin._gallery_images("aiocqhttp", "100", "猫猫")), 1)
        stats = self.plugin.stats_store.overview("aiocqhttp", "100", recent_days=7, top_users=10)
        self.assertEqual(stats["additions"], 1)

    def test_deleted_file_is_removed_from_index_without_erasing_historical_contribution(self) -> None:
        payload = png_bytes("red")
        path, _ = asyncio.run(self.plugin._store_image(FakeImage(data=payload), "aiocqhttp", "100", "猫猫", user_id="200"))
        digest = plugin_main.hashlib.md5(payload).hexdigest()
        path.unlink()
        self.assertEqual(self.plugin._gallery_images("aiocqhttp", "100", "猫猫"), [])
        self.assertEqual(self.plugin.gallery_md5_index[("aiocqhttp", "100", "猫猫")], {})
        self.assertIsNone(self.plugin.stats_store.image_record("aiocqhttp", "100", "猫猫", digest))
        self.assertEqual(self.plugin.stats_store.overview("aiocqhttp", "100", recent_days=7, top_users=10)["additions"], 1)


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

    def test_deleting_legacy_filename_invalidates_its_content_based_metadata(self) -> None:
        self.add_same_image("猫猫")
        digest = plugin_main.hashlib.md5(MEDIA_BYTES["quoted"]).hexdigest()
        self.plugin.stats_store.record("IMAGE_ADDED", "aiocqhttp", "100", "猫猫", image_md5=digest, user_id="111")
        event = FakeEvent("删除 猫猫", admin=True)
        event.quoted_images = ["quoted"]
        run_handler(self.plugin, event)
        self.assertIsNone(self.plugin.stats_store.image_record("aiocqhttp", "100", "猫猫", digest))

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
        self.assertIn("`权重 猫猫 0.5`", readme)
        self.assertIn("`来只统计`", readme)
        self.assertIn("回复图片并发送 `删除 图库名`", readme)
        self.assertIn("`#清理 图库名`", readme)
        self.assertIn("近期未出现", readme)
        self.assertNotIn("仅 AstrBot 管理员可用", readme)
        self.assertNotIn("`删除 猫猫` 或 `#清理 猫猫`", readme)


if __name__ == "__main__":
    unittest.main()
