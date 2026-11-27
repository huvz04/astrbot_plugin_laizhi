from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stats_store import GalleryStatsStore


class GalleryStatsStoreTests(unittest.TestCase):
    def test_annual_data_uses_beijing_year_and_persistent_group_votes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.db"
            store = GalleryStatsStore(path)
            for date, count in (("2025-12-31T15:59:59+00:00", 1),
                                ("2025-12-31T16:00:00+00:00", 20),
                                ("2026-12-31T15:59:59+00:00", 10),
                                ("2026-12-31T16:00:00+00:00", 5)):
                store.record("GALLERY_CALLED", "qq", "100", "猫猫", image_count=count,
                             created_at=datetime.fromisoformat(date))
            store.record("IMAGE_ADDED", "qq", "100", "猫猫",
                         created_at=datetime.fromisoformat("2026-02-01T04:00:00+00:00"))
            data = store.annual_stats("qq", "100", 2026)
            self.assertEqual((data["draws"], data["additions"], data["popular"]), (30, 1, [("猫猫", 30)]))
            for nominee in ("11", "2", "51", "5"):
                store.cast_vote("qq", "100", 2026, "摄影", nominee, nominee, "")
            for platform, group, year in (("qq", "200", 2026), ("foo", "100", 2026), ("qq", "100", 2025)):
                store.cast_vote(platform, group, year, "摄影", "123", "51", "")
            leaders = GalleryStatsStore(path).vote_leaders("qq", "100", 2026)
            self.assertEqual([(r["nominee"], r["votes"]) for r in leaders], [("11", 1), ("2", 1), ("51", 1)])

    def test_nomination_order_survives_revote_migration_and_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.db"
            store = GalleryStatsStore(path)
            scope = ("qq", "100", 2026)
            for category, voter, nominee in [("Z", "1", "99"), ("A", "2", "33"), ("Z", "3", "22")]:
                store.cast_vote(*scope, category, voter, nominee, "")
            # Simulate the prior schema and reopen it with existing ballots.
            with closing(store._connect()) as db:
                db.execute("DROP TABLE annual_nominations")
            store = GalleryStatsStore(path)
            store.cast_vote(*scope, "Z", "1", "22", "")
            store.cast_vote(*scope, "Z", "4", "99", "")
            self.assertEqual([r["nominee"] for r in store.vote_leaders(*scope, "Z")], ["22", "99"])
            store.cast_vote(*scope, "Z", "1", "11", "")
            self.assertEqual([r["nominee"] for r in store.vote_leaders(*scope, "Z")], ["99", "22", "11"])
            self.assertEqual([c for c, _ in store.all_boards(*scope)], ["", "Z", "A"])
            store.delete_board(*scope, "Z")
            store.cast_vote(*scope, "Z", "1", "99", "")
            self.assertEqual([c for c, _ in store.all_boards(*scope)], ["", "A", "Z"])

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

    def test_historical_batch_draws_are_summed_by_image_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "stats.db"
            store = GalleryStatsStore(database_path)
            for count in (20, 10, 1):
                store.record("GALLERY_CALLED", "qq", "100", "猫猫", image_count=count)
            store.record("GALLERY_CALLED", "qq", "100", "狗狗", image_count=35)
            store.record("GALLERY_CALLED", "qq", "other", "猫猫", image_count=1000)
            store.record("GALLERY_CALLED", "other", "100", "猫猫", image_count=1000)
            store.record("GALLERY_CALLED", "qq", "100", "统计", image_count=1000)
            store.record(
                "GALLERY_CALLED", "qq", "100", "旧图库", image_count=1000,
                created_at=datetime.now(timezone.utc) - timedelta(days=8),
            )
            store.record("IMAGE_ADDED", "qq", "100", "猫猫")

            overview = GalleryStatsStore(database_path).overview(
                "qq", "100", recent_days=7, top_users=10,
            )

            self.assertEqual(
                [(row["gallery_name"], row["count"]) for row in overview["popular"]],
                [("狗狗", 35), ("猫猫", 31)],
            )
            self.assertEqual(overview["recent_calls"], 66)
            self.assertEqual(overview["additions"], 1)

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
