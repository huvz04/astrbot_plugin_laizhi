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

from stats_store import GalleryStatsStore

PLUGIN_NAME = "astrbot_plugin_laizhi"
IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_DRAW_COUNT = 20
PENDING_SECONDS = 30


class Main(star.Star):
    """Store and draw group-specific image galleries."""

    def __init__(self, context: star.Context, config=None) -> None:
        """Initialize the plugin.

        Args:
            context: AstrBot plugin context.
        """
        super().__init__(context, config)
        self.config = config or {}
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pending_additions: dict[tuple[str, str, str], tuple[str, float]] = {}
        self.draw_history: dict[tuple[str, str, str], set[str]] = {}
        self.random_gallery_history: dict[tuple[str, str], set[str]] = {}
        self.gallery_md5_index: dict[tuple[str, str, str], dict[str, Path]] = {}
        try:
            self.stats_store: GalleryStatsStore | None = GalleryStatsStore(
                self.data_dir / "gallery_stats.db"
            )
        except Exception:
            logger.exception("Failed to initialize gallery statistics")
            self.stats_store = None

    def _max_draw_count(self) -> int:
        """Return the configured positive multi-draw limit."""
        try:
            value = int(
                self.config.get("max_draw_count", DEFAULT_MAX_DRAW_COUNT)
            )
        except (TypeError, ValueError):
            return DEFAULT_MAX_DRAW_COUNT
        return value if value >= 1 else DEFAULT_MAX_DRAW_COUNT

    def _config_int(
        self,
        key: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return min(max(value, minimum), maximum)

    async def _record_stat(self, event_type: str, **values) -> None:
        if self.stats_store is None:
            return
        try:
            await asyncio.to_thread(self.stats_store.record, event_type, **values)
        except Exception:
            logger.exception("Failed to persist gallery statistics")

    @staticmethod
    def _sender_identity(event: AstrMessageEvent) -> tuple[str, str]:
        sender_id = str(event.get_sender_id())
        try:
            sender_name = str(event.get_sender_name() or "").strip()
        except Exception:
            sender_name = ""
        return sender_id, sender_name or sender_id

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
        gallery_key = (platform_id, group_id, gallery_name)
        md5_index = self.gallery_md5_index.get(gallery_key)
        if md5_index is None:

            def build_md5_index() -> dict[str, Path]:
                """Build a content hash index, including legacy SHA-named files."""
                index: dict[str, Path] = {}
                for existing_path in gallery_dir.iterdir():
                    if (
                        not existing_path.is_file()
                        or existing_path.suffix.lower() not in IMAGE_SUFFIXES
                    ):
                        continue
                    try:
                        existing_digest = hashlib.md5(
                            existing_path.read_bytes(),
                            usedforsecurity=False,
                        ).hexdigest()
                    except OSError:
                        continue
                    index.setdefault(existing_digest, existing_path)
                return index

            md5_index = await asyncio.to_thread(build_md5_index)
            self.gallery_md5_index[gallery_key] = md5_index

        digest = hashlib.md5(image_bytes, usedforsecurity=False).hexdigest()
        existing_path = md5_index.get(digest)
        if existing_path and existing_path.is_file():
            return existing_path, False

        image_path = gallery_dir / f"{digest}{suffix}"

        def write_exclusive() -> bool:
            """Write bytes without overwriting a concurrently added duplicate."""
            try:
                with image_path.open("xb") as image_file:
                    image_file.write(image_bytes)
            except FileExistsError:
                return False
            return True

        is_new = await asyncio.to_thread(write_exclusive)
        md5_index[digest] = image_path
        return image_path, is_new

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

    def _non_empty_galleries(
        self,
        platform_id: str,
        group_id: str,
    ) -> list[tuple[str, list[Path]]]:
        """List non-empty galleries for one platform group in name order."""
        group_dir = self.data_dir / platform_id / group_id
        if not group_dir.is_dir():
            return []

        galleries: list[tuple[str, list[Path]]] = []
        for gallery_dir in sorted(group_dir.iterdir(), key=lambda path: path.name):
            if not gallery_dir.is_dir():
                continue
            images = self._gallery_images(
                platform_id,
                group_id,
                gallery_dir.name,
            )
            if images:
                galleries.append((gallery_dir.name, images))
        return galleries

    async def _overview_data(self, platform_id: str, group_id: str) -> dict:
        recent_days = self._config_int("overview_recent_days", 7, 1, 365)
        top_users = self._config_int("overview_top_users", 10, 1, 50)
        max_galleries = self._config_int("overview_max_galleries", 50, 1, 200)
        galleries = sorted(
            (
                {"name": name, "count": len(images)}
                for name, images in self._non_empty_galleries(platform_id, group_id)
            ),
            key=lambda item: (-item["count"], item["name"]),
        )
        try:
            if self.stats_store is None:
                raise RuntimeError("gallery statistics are unavailable")
            stats = await asyncio.to_thread(
                self.stats_store.overview,
                platform_id,
                group_id,
                recent_days=recent_days,
                top_users=top_users,
            )
        except Exception:
            logger.exception("Failed to load gallery statistics")
            stats = {
                "contributors": [],
                "popular": [],
                "additions": 0,
                "recent_calls": 0,
            }
        stats["popular"] = stats["popular"][:max_galleries]
        return {
            **stats,
            "recent_days": recent_days,
            "gallery_count": len(galleries),
            "image_count": sum(item["count"] for item in galleries),
            "galleries": galleries[:max_galleries],
        }

    @staticmethod
    def _overview_text(data: dict) -> str:
        lines = [
            "本群图库总览",
            (
                f"图库：{data['gallery_count']} 个｜图片：{data['image_count']} 张｜"
                f"近 {data['recent_days']} 天调用：{data['recent_calls']} 次"
            ),
            "",
            "添加贡献榜",
        ]
        contributors = data["contributors"]
        lines.extend(
            f"{index}. {item['name']}  {item['count']} 张"
            for index, item in enumerate(contributors, start=1)
        )
        if not contributors:
            lines.append("暂无记录")
        lines.extend(["", "图库规模榜"])
        lines.extend(
            f"{index}. {item['name']}  {item['count']} 张"
            for index, item in enumerate(data["galleries"], start=1)
        )
        lines.extend(["", f"近 {data['recent_days']} 天热门榜"])
        popular = data["popular"]
        lines.extend(
            f"{index}. {item['gallery_name']}  {item['count']} 次"
            for index, item in enumerate(popular, start=1)
        )
        if not popular:
            lines.append("暂无记录")
        lines.extend(["", "贡献与调用数据自统计功能启用后开始记录。"])
        return "\n".join(lines)

    async def _render_overview(self, data: dict) -> str:
        template_path = Path(__file__).parent / "templates" / "overview.html"
        template = await asyncio.to_thread(template_path.read_text, encoding="utf-8")
        width = self._config_int("overview_render_width", 1200, 800, 1800)
        return await self.html_render(
            template,
            data,
            return_url=False,
            options={
                "viewport": {"width": width, "height": 900},
                "type": "png",
                "full_page": True,
            },
        )

    def _random_gallery_image(
        self,
        platform_id: str,
        group_id: str,
    ) -> tuple[str, Path] | None:
        """Draw every group image at equal weight without repeats per cycle."""
        entries = [
            (gallery_name, path)
            for gallery_name, images in self._non_empty_galleries(
                platform_id,
                group_id,
            )
            for path in images
        ]
        if not entries:
            self.random_gallery_history.pop((platform_id, group_id), None)
            return None

        history_key = (platform_id, group_id)
        used_paths = self.random_gallery_history.setdefault(history_key, set())
        current_paths = {str(path.resolve()) for _, path in entries}
        used_paths.intersection_update(current_paths)
        available = [
            (gallery_name, path)
            for gallery_name, path in entries
            if str(path.resolve()) not in used_paths
        ]
        if not available:
            used_paths.clear()
            available = entries

        selected = random.choice(available)
        used_paths.add(str(selected[1].resolve()))
        return selected

    async def _delete_quoted_image(
        self,
        source: str,
        platform_id: str,
        group_id: str,
        gallery_name: str,
    ) -> Path | None:
        """Delete the quoted image only from the explicitly named gallery."""
        image_bytes = await MediaResolver(source, media_type="image").to_bytes()
        digest = hashlib.md5(
            image_bytes,
            usedforsecurity=False,
        ).hexdigest()
        gallery_key = (platform_id, group_id, gallery_name)
        images = self._gallery_images(platform_id, group_id, gallery_name)
        for path in images:
            try:
                existing_bytes = await asyncio.to_thread(path.read_bytes)
            except OSError:
                continue
            existing_digest = hashlib.md5(
                existing_bytes,
                usedforsecurity=False,
            ).hexdigest()
            if existing_digest != digest:
                continue
            await asyncio.to_thread(path.unlink)
            self.gallery_md5_index.pop(gallery_key, None)
            self.draw_history.pop(gallery_key, None)
            return path
        return None

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
                        added_by_id, added_by_name = self._sender_identity(event)
                        await self._record_stat(
                            "IMAGE_ADDED",
                            platform_id=platform_id,
                            group_id=group_id,
                            gallery_name=gallery_name,
                            image_md5=image_path.stem,
                            user_id=added_by_id,
                            user_name=added_by_name,
                        )
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
                    added_by_id, added_by_name = self._sender_identity(event)
                    await self._record_stat(
                        "IMAGE_ADDED",
                        platform_id=platform_id,
                        group_id=group_id,
                        gallery_name=gallery_name,
                        image_md5=image_path.stem,
                        user_id=added_by_id,
                        user_name=added_by_name,
                    )
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

        if text in {"随机来点", "随机来只"}:
            event.stop_event()
            selected = self._random_gallery_image(platform_id, group_id)
            if selected is None:
                yield event.plain_result("当前群还没有可用的图库图片。")
                return

            gallery_name, image_path = selected
            await self._record_stat(
                "GALLERY_CALLED",
                platform_id=platform_id,
                group_id=group_id,
                gallery_name=gallery_name,
                user_id=sender_id,
                user_name=self._sender_identity(event)[1],
                command_type=text,
            )
            yield event.chain_result(
                [
                    Comp.Image.fromFileSystem(str(image_path.resolve())),
                    Comp.Plain(f"🎲 随机来自图库「{gallery_name}」"),
                ]
            )
            return

        if text in {"预览全部", "图库统计"}:
            event.stop_event()
            overview_data = await self._overview_data(platform_id, group_id)
            try:
                overview_path = await self._render_overview(overview_data)
                if not overview_path:
                    raise ValueError("html_render returned an empty path")
                yield event.image_result(str(overview_path))
            except Exception:
                logger.exception("Failed to render gallery overview")
                yield event.plain_result(self._overview_text(overview_data))
            return

        delete_image_match = re.fullmatch(r"删除\s*(.+)", text)
        if delete_image_match:
            event.stop_event()
            if not event.is_admin():
                yield event.plain_result(
                    "只有管理员可以删除图库图片。"
                )
                return

            gallery_name = delete_image_match.group(1).strip()
            if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff]{1,30}", gallery_name):
                yield event.plain_result("图库名不合法。")
                return

            try:
                quoted_images = await extract_quoted_message_images(event)
            except Exception:
                logger.exception("Failed to resolve the replied image for deletion")
                yield event.plain_result("读取被回复的图片失败，请稍后再试。")
                return
            if not quoted_images:
                yield event.plain_result(
                    "请回复一张图库图片并发送“删除 图库名”。"
                )
                return

            try:
                deleted_path = await self._delete_quoted_image(
                    quoted_images[0],
                    platform_id,
                    group_id,
                    gallery_name,
                )
            except Exception:
                logger.exception("Failed to delete the replied gallery image")
                yield event.plain_result("读取被回复的图片失败，请稍后再试。")
                return
            if deleted_path is None:
                yield event.plain_result(
                    f"“{gallery_name}”图库中没有找到这张图片。"
                )
                return

            deleted_by_id, deleted_by_name = self._sender_identity(event)
            await self._record_stat(
                "IMAGE_DELETED",
                platform_id=platform_id,
                group_id=group_id,
                gallery_name=gallery_name,
                image_md5=deleted_path.stem,
                user_id=deleted_by_id,
                user_name=deleted_by_name,
            )
            yield event.plain_result(
                f"已从“{gallery_name}”图库删除这张图片。"
            )
            return

        clean_match = re.fullmatch(r"#清理\s*(.+)", text)
        if clean_match:
            event.stop_event()
            if not event.is_admin():
                yield event.plain_result("只有管理员可以删除图库。")
                return

            gallery_name = clean_match.group(1).strip()
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
            self.gallery_md5_index.pop((platform_id, group_id, gallery_name), None)
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

            await self._record_stat(
                "GALLERY_CALLED",
                platform_id=platform_id,
                group_id=group_id,
                gallery_name=gallery_name,
                user_id=sender_id,
                user_name=self._sender_identity(event)[1],
                command_type=get_match.group(0),
            )
            yield event.image_result(str(random.choice(images).resolve()))
            return

        draw_match = re.fullmatch(r"抽\s*(\d+)\s+(.+)", text)
        if draw_match:
            event.stop_event()
            count = int(draw_match.group(1))
            gallery_name = draw_match.group(2).strip()
            max_draw_count = self._max_draw_count()
            if not 1 <= count <= max_draw_count:
                yield event.plain_result(
                    f"抽取数量必须在 1 到 {max_draw_count} 之间。"
                )
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
            await self._record_stat(
                "GALLERY_CALLED",
                platform_id=platform_id,
                group_id=group_id,
                gallery_name=gallery_name,
                user_id=sender_id,
                user_name=self._sender_identity(event)[1],
                command_type="抽",
                image_count=len(selected),
            )
            yield event.chain_result([Comp.Nodes(nodes)])
