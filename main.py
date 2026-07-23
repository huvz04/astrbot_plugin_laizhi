from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import random
import re
import shutil
import time
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.utils.media_utils import MediaResolver
from astrbot.core.utils.quoted_message import extract_quoted_message_images
from PIL import Image as PillowImage

PLUGIN_NAME = "astrbot_plugin_laizhi"
IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_DRAW_COUNT = 20
PENDING_SECONDS = 30


class Main(star.Star):
    """Store and draw group-specific image galleries."""

    def __init__(self, context: star.Context) -> None:
        """Initialize the plugin.

        Args:
            context: AstrBot plugin context.
        """
        super().__init__(context)
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pending_additions: dict[tuple[str, str, str], tuple[str, float]] = {}
        self.draw_history: dict[tuple[str, str, str], set[str]] = {}

    async def _store_image(
        self,
        source: Comp.Image | str,
        platform_id: str,
        group_id: str,
        gallery_name: str,
    ) -> tuple[Path, bool]:
        """Validate, deduplicate, and store one image.

        Args:
            source: Incoming image component or resolved image reference.
            platform_id: Sanitized platform identifier.
            group_id: Sanitized group identifier.
            gallery_name: Validated gallery name.

        Returns:
            Stored image path and whether a new image was written. The boolean is
            False when the image was already present.

        Raises:
            ValueError: The payload is empty, too large, or not a supported image.
        """
        if isinstance(source, Comp.Image):
            encoded = await source.convert_to_base64()
            image_bytes = base64.b64decode(encoded, validate=True)
        else:
            image_bytes = await MediaResolver(source, media_type="image").to_bytes()

        if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError("图片为空或超过 20 MiB")

        with PillowImage.open(io.BytesIO(image_bytes)) as image:
            image.verify()
            suffix = {
                "GIF": ".gif",
                "JPEG": ".jpg",
                "PNG": ".png",
                "WEBP": ".webp",
            }.get(image.format or "")
        if not suffix:
            raise ValueError("仅支持 JPEG、PNG、GIF 和 WebP 图片")

        gallery_dir = self.data_dir / platform_id / group_id / gallery_name
        gallery_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(image_bytes).hexdigest()
        image_path = gallery_dir / f"{digest}{suffix}"

        def write_exclusive() -> bool:
            """Write bytes without overwriting a concurrently added duplicate."""
            try:
                with image_path.open("xb") as image_file:
                    image_file.write(image_bytes)
            except FileExistsError:
                return False
            return True

        return image_path, await asyncio.to_thread(write_exclusive)

    def _gallery_images(
        self,
        platform_id: str,
        group_id: str,
        gallery_name: str,
    ) -> list[Path]:
        """List supported images in a gallery.

        Args:
            platform_id: Sanitized platform identifier.
            group_id: Sanitized group identifier.
            gallery_name: Validated gallery name.

        Returns:
            Image paths found in the gallery.
        """
        gallery_dir = self.data_dir / platform_id / group_id / gallery_name
        if not gallery_dir.is_dir():
            return []
        return [
            path
            for path in gallery_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=10)
    async def on_group_message(self, event: AstrMessageEvent):
        """Handle gallery commands and pending image additions.

        Args:
            event: Incoming group message event.

        Yields:
            AstrBot message results for recognized commands.
        """
        message_components = event.get_messages()
        plain_text = "".join(
            component.text
            for component in message_components
            if isinstance(component, Comp.Plain)
        )
        text = plain_text.strip() or event.message_str.strip()
        platform_id = (
            re.sub(r"[^A-Za-z0-9_-]", "_", str(event.get_platform_name()))[:80]
            or "unknown"
        )
        group_id = (
            re.sub(r"[^A-Za-z0-9_-]", "_", str(event.get_group_id()))[:80] or "unknown"
        )
        sender_id = str(event.get_sender_id())
        pending_key = (platform_id, group_id, sender_id)
        now = time.monotonic()

        for key, (_, deadline) in tuple(self.pending_additions.items()):
            if deadline <= now:
                self.pending_additions.pop(key, None)

        direct_images = [
            component
            for component in message_components
            if isinstance(component, Comp.Image)
        ]

        add_match = re.fullmatch(r"(?:添加|add)\s*(.*)", text, re.IGNORECASE)
        if add_match:
            event.stop_event()
            gallery_name = add_match.group(1).strip()
            if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff]{1,30}", gallery_name):
                yield event.plain_result(
                    "请使用“添加 图库名”，图库名只能包含中英文和数字，最长 30 字。"
                )
                return

            image_sources: list[Comp.Image | str] = list(direct_images)
            if not image_sources:
                try:
                    image_sources.extend(await extract_quoted_message_images(event))
                except Exception:
                    logger.exception("Failed to resolve the replied image")
                    yield event.plain_result(
                        "读取被回复的图片失败，请直接发送图片再试。"
                    )
                    return

            if not image_sources:
                self.pending_additions[pending_key] = (
                    gallery_name,
                    now + PENDING_SECONDS,
                )
                yield event.plain_result("请在 30 秒内发送一张图片。")
                return

            saved_count = 0
            duplicate_count = 0
            saved_paths: list[Path] = []
            try:
                for source in image_sources:
                    image_path, is_new = await self._store_image(
                        source,
                        platform_id,
                        group_id,
                        gallery_name,
                    )
                    if is_new:
                        saved_count += 1
                        saved_paths.append(image_path)
                    else:
                        duplicate_count += 1
            except Exception as exc:
                logger.exception("Failed to save gallery image")
                yield event.plain_result(f"图片保存失败：{exc}")
                return

            if saved_count:
                duplicate_note = (
                    f"，另有 {duplicate_count} 张重复图片已跳过"
                    if duplicate_count
                    else ""
                )
                yield event.chain_result(
                    [
                        *[
                            Comp.Image.fromFileSystem(str(path.resolve()))
                            for path in saved_paths
                        ],
                        Comp.Plain(f"已添加到{gallery_name}{duplicate_note}"),
                    ]
                )
            else:
                yield event.plain_result("这张图片已经在图库里了。")
            return

        pending = self.pending_additions.get(pending_key)
        if pending and direct_images:
            event.stop_event()
            gallery_name, deadline = pending
            if deadline <= now:
                self.pending_additions.pop(pending_key, None)
                yield event.plain_result("添加已超时，请重新发送“添加 图库名”。")
                return

            image_source = direct_images[0]
            try:
                image_path, is_new = await self._store_image(
                    image_source,
                    platform_id,
                    group_id,
                    gallery_name,
                )
                if is_new:
                    result = event.chain_result(
                        [
                            Comp.Image.fromFileSystem(str(image_path.resolve())),
                            Comp.Plain(f"已添加到{gallery_name}"),
                        ]
                    )
                else:
                    result = event.plain_result("这张图片已经在图库里了。")
            except Exception as exc:
                logger.exception("Failed to save a pending gallery image")
                yield event.plain_result(f"图片保存失败：{exc}")
                return
            finally:
                self.pending_additions.pop(pending_key, None)

            yield result
            return

        delete_match = re.fullmatch(r"(?:删除|#清理)\s*(.+)", text)
        if delete_match:
            event.stop_event()
            if not event.is_admin():
                yield event.plain_result("只有 AstrBot 管理员可以删除图库。")
                return

            gallery_name = delete_match.group(1).strip()
            if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff]{1,30}", gallery_name):
                yield event.plain_result("图库名不合法。")
                return

            gallery_dir = self.data_dir / platform_id / group_id / gallery_name
            if not gallery_dir.is_dir():
                yield event.plain_result(f"“{gallery_name}”图库不存在。")
                return

            image_count = len(self._gallery_images(platform_id, group_id, gallery_name))
            await asyncio.to_thread(shutil.rmtree, gallery_dir)
            self.draw_history.pop((platform_id, group_id, gallery_name), None)
            yield event.plain_result(
                f"已删除“{gallery_name}”图库，共清理 {image_count} 张图片。"
            )
            return

        get_match = re.fullmatch(r"(?:来只|来点)\s*(.+)", text)
        if get_match:
            event.stop_event()
            gallery_name = get_match.group(1).strip()
            if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff]{1,30}", gallery_name):
                yield event.plain_result("图库名不合法。")
                return

            images = self._gallery_images(platform_id, group_id, gallery_name)
            if not images:
                yield event.plain_result(f"“{gallery_name}”图库里还没有图片。")
                return

            yield event.image_result(str(random.choice(images).resolve()))
            return

        draw_match = re.fullmatch(r"抽\s*(\d+)\s+(.+)", text)
        if draw_match:
            event.stop_event()
            count = int(draw_match.group(1))
            gallery_name = draw_match.group(2).strip()
            if not 1 <= count <= MAX_DRAW_COUNT:
                yield event.plain_result(f"抽取数量必须在 1 到 {MAX_DRAW_COUNT} 之间。")
                return
            if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff]{1,30}", gallery_name):
                yield event.plain_result("图库名不合法。")
                return

            images = self._gallery_images(platform_id, group_id, gallery_name)
            if not images:
                yield event.plain_result(f"“{gallery_name}”图库里还没有图片。")
                return

            actual_count = min(count, len(images))
            history_key = (platform_id, group_id, gallery_name)
            used_paths = self.draw_history.setdefault(history_key, set())
            current_paths = {str(path.resolve()) for path in images}
            used_paths.intersection_update(current_paths)

            available = [
                path for path in images if str(path.resolve()) not in used_paths
            ]
            first_batch_count = min(actual_count, len(available))
            selected = random.sample(available, first_batch_count)
            remaining_count = actual_count - first_batch_count

            if remaining_count:
                new_cycle_pool = [path for path in images if path not in selected]
                new_cycle_selected = random.sample(new_cycle_pool, remaining_count)
                selected.extend(new_cycle_selected)
                used_paths.clear()
                used_paths.update(str(path.resolve()) for path in new_cycle_selected)
            else:
                used_paths.update(str(path.resolve()) for path in selected)
                if len(used_paths) >= len(images):
                    used_paths.clear()

            shortfall_note = (
                f"（图库总共 {len(images)} 张）" if len(selected) < count else ""
            )
            nodes = [
                Comp.Node(
                    uin=event.get_self_id(),
                    name="来只图库",
                    content=[
                        Comp.Plain(
                            f"🎲 从图库「{gallery_name}」抽取了 {len(selected)} 张图片"
                            f"{shortfall_note}"
                        )
                    ],
                )
            ]
            nodes.extend(
                Comp.Node(
                    uin=event.get_self_id(),
                    name="来只图库",
                    content=[
                        Comp.Plain(f"第 {index} 张"),
                        Comp.Image.fromFileSystem(str(path.resolve())),
                    ],
                )
                for index, path in enumerate(selected, start=1)
            )
            yield event.chain_result([Comp.Nodes(nodes)])
