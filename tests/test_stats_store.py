from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stats_store import GalleryStatsStore


class GalleryStatsStoreTests(unittest.TestCase):
    def test_image_record_does_not_reuse_provenance_after_delete_or_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GalleryStatsStore(Path(directory) / "stats.db")
            for removal in ("IMAGE_DELETED", "IMAGE_MISSING", "GALLERY_CLEARED"):
                store.record("IMAGE_ADDED", "qq", "100", "猫猫", image_md5="digest", user_id="first")
                store.record("IMAGE_ADDED", "qq", "other", "猫猫", image_md5="digest", user_id="other")
                self.assertEqual(store.image_record("qq", "100", "猫猫", "digest")["user_id"], "first")
                store.record(removal, "qq", "100", "猫猫", image_md5="digest")
                self.assertIsNone(store.image_record("qq", "100", "猫猫", "digest"))
                store.record("IMAGE_ADDED", "qq", "100", "猫猫", image_md5="digest", user_id="new")
                self.assertEqual(store.image_record("qq", "100", "猫猫", "digest")["user_id"], "new")
                store.record("GALLERY_CLEARED", "qq", "100", "猫猫")

    def test_reserved_gallery_is_excluded_from_historical_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GalleryStatsStore(Path(directory) / "stats.db")
            store.record("IMAGE_ADDED", "qq", "100", "统计", user_id="1")
            store.record("GALLERY_CALLED", "qq", "100", "统计")
            overview = store.overview("qq", "100", recent_days=7, top_users=10)
            self.assertEqual(overview["contributors"], [])
            self.assertEqual(overview["popular"], [])
            self.assertEqual(overview["additions"], 0)
            self.assertEqual(overview["recent_calls"], 0)

    def test_rankings_are_group_isolated_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GalleryStatsStore(Path(directory) / "stats.db")
            for name in ("旧昵称", "新昵称"):
                store.record(
                    "IMAGE_ADDED", "qq", "100", "猫猫",
                    user_id="1", user_name=name,
                )
            store.record(
                "IMAGE_ADDED", "qq", "100", "狗狗",
                user_id="2", user_name="用户乙",
            )
            store.record(
                "IMAGE_ADDED", "qq", "other", "猫猫",
                user_id="3", user_name="其他群",
            )

            overview = store.overview("qq", "100", recent_days=7, top_users=10)

            self.assertEqual(
                overview["contributors"],
                [
                    {"user_id": "1", "count": 2, "name": "新昵称"},
                    {"user_id": "2", "count": 1, "name": "用户乙"},
                ],
            )
            self.assertEqual(overview["additions"], 3)

    def test_recent_calls_exclude_old_events_and_sort_by_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GalleryStatsStore(Path(directory) / "stats.db")
            now = datetime.now(timezone.utc)
            store.record("GALLERY_CALLED", "qq", "100", "猫猫", created_at=now)
            store.record("GALLERY_CALLED", "qq", "100", "猫猫", created_at=now)
            store.record("GALLERY_CALLED", "qq", "100", "狗狗", created_at=now)
            store.record(
                "GALLERY_CALLED", "qq", "100", "旧图库",
                created_at=now - timedelta(days=8),
            )

            overview = store.overview("qq", "100", recent_days=7, top_users=10)

            self.assertEqual(
                [(row["gallery_name"], row["count"]) for row in overview["popular"]],
                [("猫猫", 2), ("狗狗", 1)],
            )
            self.assertEqual(overview["recent_calls"], 3)


if __name__ == "__main__":
    unittest.main()
