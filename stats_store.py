from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class GalleryStatsStore:
    """Persist group-isolated gallery events and aggregate rankings."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS gallery_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    platform_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    gallery_name TEXT NOT NULL,
                    image_md5 TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    user_name TEXT NOT NULL DEFAULT '',
                    command_type TEXT NOT NULL DEFAULT '',
                    image_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_gallery_events_group_type_time
                    ON gallery_events(platform_id, group_id, event_type, created_at);
                CREATE INDEX IF NOT EXISTS idx_gallery_events_group_gallery
                    ON gallery_events(platform_id, group_id, gallery_name);
                """
            )

    def record(
        self,
        event_type: str,
        platform_id: str,
        group_id: str,
        gallery_name: str,
        *,
        image_md5: str = "",
        user_id: str = "",
        user_name: str = "",
        command_type: str = "",
        image_count: int = 1,
        created_at: datetime | None = None,
    ) -> None:
        timestamp = (created_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        ).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gallery_events (
                    event_type, platform_id, group_id, gallery_name,
                    image_md5, user_id, user_name, command_type,
                    image_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    platform_id,
                    group_id,
                    gallery_name,
                    image_md5,
                    user_id,
                    user_name,
                    command_type,
                    max(1, int(image_count)),
                    timestamp,
                ),
            )

    def overview(
        self,
        platform_id: str,
        group_id: str,
        *,
        recent_days: int,
        top_users: int,
    ) -> dict:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=recent_days)).isoformat()
        with self._connect() as connection:
            contributors = connection.execute(
                """
                SELECT user_id, COUNT(*) AS count,
                       COALESCE((
                           SELECT NULLIF(latest.user_name, '')
                           FROM gallery_events AS latest
                           WHERE latest.platform_id = events.platform_id
                             AND latest.group_id = events.group_id
                             AND latest.user_id = events.user_id
                             AND latest.user_name <> ''
                           ORDER BY latest.id DESC LIMIT 1
                       ), user_id, '未知用户') AS name
                FROM gallery_events AS events
                WHERE platform_id = ? AND group_id = ?
                  AND event_type = 'IMAGE_ADDED'
                GROUP BY user_id
                ORDER BY count DESC, name ASC
                LIMIT ?
                """,
                (platform_id, group_id, top_users),
            ).fetchall()
            popular = connection.execute(
                """
                SELECT gallery_name, COUNT(*) AS count, MAX(created_at) AS last_called_at
                FROM gallery_events
                WHERE platform_id = ? AND group_id = ?
                  AND event_type = 'GALLERY_CALLED' AND created_at >= ?
                GROUP BY gallery_name
                ORDER BY count DESC, last_called_at DESC, gallery_name ASC
                """,
                (platform_id, group_id, cutoff),
            ).fetchall()
            totals = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN event_type = 'IMAGE_ADDED' THEN 1 ELSE 0 END)
                        AS additions,
                    SUM(CASE WHEN event_type = 'GALLERY_CALLED' AND created_at >= ?
                             THEN 1 ELSE 0 END) AS recent_calls
                FROM gallery_events
                WHERE platform_id = ? AND group_id = ?
                """,
                (cutoff, platform_id, group_id),
            ).fetchone()
        return {
            "contributors": [dict(row) for row in contributors],
            "popular": [dict(row) for row in popular],
            "additions": int(totals["additions"] or 0),
            "recent_calls": int(totals["recent_calls"] or 0),
        }
