from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))

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
                CREATE INDEX IF NOT EXISTS idx_gallery_events_image
                    ON gallery_events(platform_id, group_id, gallery_name, image_md5, id);
                CREATE TABLE IF NOT EXISTS annual_votes (
                    platform_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    voter TEXT NOT NULL,
                    nominee TEXT NOT NULL,
                    name TEXT NOT NULL,
                    PRIMARY KEY (platform_id, group_id, year, category, voter)
                );
                CREATE TABLE IF NOT EXISTS annual_broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    origin TEXT NOT NULL, bot_id TEXT NOT NULL,
                    send_at INTEGER NOT NULL, next_try INTEGER NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(platform_id, group_id)
                );
                CREATE TABLE IF NOT EXISTS annual_nominations (
                    id INTEGER PRIMARY KEY,
                    platform_id TEXT NOT NULL, group_id TEXT NOT NULL, year INTEGER NOT NULL,
                    category TEXT NOT NULL, nominee TEXT NOT NULL,
                    UNIQUE(platform_id, group_id, year, category, nominee)
                );
                """
            )
            # Older votes retain the insertion order of voter slots, but not change history.
            if not connection.execute("SELECT 1 FROM annual_nominations LIMIT 1").fetchone():
                connection.execute("""
                    INSERT OR IGNORE INTO annual_nominations(platform_id, group_id, year, category, nominee)
                    SELECT platform_id, group_id, year, category, nominee FROM annual_votes
                    GROUP BY platform_id, group_id, year, category, nominee ORDER BY MIN(rowid)
                """)

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

    def image_record(self, platform_id: str, group_id: str, gallery_name: str, image_md5: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_id, user_name, created_at
                FROM gallery_events
                WHERE platform_id = ? AND group_id = ? AND gallery_name = ?
                  AND image_md5 = ? AND event_type = 'IMAGE_ADDED'
                  AND id > COALESCE((
                      SELECT MAX(id) FROM gallery_events
                      WHERE platform_id = ? AND group_id = ? AND gallery_name = ?
                        AND (event_type = 'GALLERY_CLEARED'
                             OR (image_md5 = ? AND event_type IN ('IMAGE_DELETED', 'IMAGE_MISSING')))
                  ), 0)
                ORDER BY id ASC LIMIT 1
                """,
                (platform_id, group_id, gallery_name, image_md5,
                 platform_id, group_id, gallery_name, image_md5),
            ).fetchone()
        return dict(row) if row else None

    def cast_vote(self, platform_id: str, group_id: str, year: int, category: str,
                  voter: str, nominee: str, name: str) -> None:
        self.cast_votes(platform_id, group_id, year, [category], voter, nominee, name)

    def cast_votes(self, platform_id: str, group_id: str, year: int, categories: list[str],
                   voter: str, nominee: str, name: str) -> None:
        with self._connect() as connection:
            connection.executemany("""
                INSERT OR IGNORE INTO annual_nominations(platform_id, group_id, year, category, nominee)
                VALUES (?, ?, ?, ?, ?)
            """, [(platform_id, group_id, year, category, nominee) for category in categories])
            connection.executemany(
                """
                INSERT INTO annual_votes (platform_id, group_id, year, category, voter, nominee, name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform_id, group_id, year, category, voter)
                DO UPDATE SET nominee = excluded.nominee, name = excluded.name
                """,
                [(platform_id, group_id, year, category, voter, nominee, name) for category in categories],
            )

    def vote_leaders(self, platform_id: str, group_id: str, year: int,
                     category: str = "") -> list[sqlite3.Row]:
        with self._connect() as connection:
            return self._leaders(connection, platform_id, group_id, year, category)

    @staticmethod
    def _leaders(connection, platform_id, group_id, year, category):
        scope = "platform_id = ? AND group_id = ? AND year = ?"
        params = [platform_id, group_id, year]
        if category:
            scope += " AND category = ?"
            params.append(category)
        return connection.execute(f"""
            WITH firsts AS (
                SELECT nominee, MIN(id) AS first_id FROM annual_nominations
                WHERE {scope} GROUP BY nominee
            ), counts AS (
                SELECT nominee, COALESCE(MAX(NULLIF(name, '')), nominee) AS display_name,
                       COUNT(*) AS votes FROM annual_votes WHERE {scope} GROUP BY nominee
            )
            SELECT counts.* FROM counts JOIN firsts USING (nominee)
            ORDER BY votes DESC, first_id LIMIT 3
        """, params + params).fetchall()

    def all_boards(self, platform_id, group_id, year):
        with self._connect() as connection:
            connection.execute("BEGIN")
            scope = (platform_id, group_id, year)
            categories = [r[0] for r in connection.execute(
                """SELECT category FROM annual_nominations WHERE platform_id=? AND group_id=? AND year=?
                   GROUP BY category ORDER BY MIN(id)""",
                scope)]
            if not categories:
                return []
            return [(category, self._leaders(connection, *scope, category)) for category in ["", *categories]]

    def delete_board(self, platform_id, group_id, year, category):
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM annual_nominations WHERE platform_id=? AND group_id=? AND year=? AND category=?",
                (platform_id, group_id, year, category))
            return connection.execute(
                "DELETE FROM annual_votes WHERE platform_id=? AND group_id=? AND year=? AND category=?",
                (platform_id, group_id, year, category)).rowcount

    def schedule_boards(self, platform_id, group_id, origin, bot_id, send_at):
        with self._connect() as connection:
            connection.execute("""
                INSERT OR REPLACE INTO annual_broadcasts
                    (platform_id, group_id, origin, bot_id, send_at, next_try)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (platform_id, group_id, origin, bot_id, send_at, send_at))

    def board_schedule(self, platform_id, group_id):
        with self._connect() as connection:
            return connection.execute("SELECT * FROM annual_broadcasts WHERE platform_id=? AND group_id=?",
                                      (platform_id, group_id)).fetchone()

    def cancel_schedule(self, platform_id, group_id):
        with self._connect() as connection:
            return connection.execute("DELETE FROM annual_broadcasts WHERE platform_id=? AND group_id=? AND sent=0",
                                      (platform_id, group_id)).rowcount

    def due_broadcasts(self, now):
        with self._connect() as connection:
            return connection.execute("SELECT * FROM annual_broadcasts WHERE sent=0 AND next_try<=? ORDER BY send_at",
                                      (now,)).fetchall()

    def claim_broadcast(self, job_id, now):
        # Recheck after rendering: an admin may have cancelled or replaced this schedule.
        with self._connect() as connection:
            return connection.execute("UPDATE annual_broadcasts SET next_try=? WHERE id=? AND sent=0 AND next_try<=?",
                                      (now + 300, job_id, now)).rowcount

    def finish_broadcast(self, job_id):
        with self._connect() as connection:
            connection.execute("UPDATE annual_broadcasts SET sent=1 WHERE id=?", (job_id,))

    def annual_stats(self, platform_id: str, group_id: str, year: int) -> dict:
        start, end = (datetime(y, 1, 1, tzinfo=BEIJING).astimezone(timezone.utc).isoformat()
                      for y in (year, year + 1))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT gallery_name, MIN(created_at) AS first_at,
                       SUM(CASE WHEN event_type = 'IMAGE_ADDED' THEN 1 ELSE 0 END) AS additions,
                       SUM(CASE WHEN event_type = 'GALLERY_CALLED' THEN image_count ELSE 0 END) AS draws
                FROM gallery_events
                WHERE platform_id = ? AND group_id = ? AND created_at >= ? AND created_at < ?
                  AND gallery_name <> '统计' AND event_type IN ('IMAGE_ADDED', 'GALLERY_CALLED')
                GROUP BY gallery_name
                ORDER BY draws DESC, gallery_name
                """, (platform_id, group_id, start, end),
            ).fetchall()
        return {
            "first_at": min((row["first_at"] for row in rows), default=None),
            "additions": sum(row["additions"] for row in rows),
            "draws": sum(row["draws"] for row in rows),
            "popular": [(row["gallery_name"], row["draws"]) for row in rows[:3] if row["draws"]],
        }

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
                  AND gallery_name <> '统计'
                GROUP BY user_id
                ORDER BY count DESC, name ASC
                LIMIT ?
                """,
                (platform_id, group_id, top_users),
            ).fetchall()
            popular = connection.execute(
                """
                SELECT gallery_name, SUM(image_count) AS count, MAX(created_at) AS last_called_at
                FROM gallery_events
                WHERE platform_id = ? AND group_id = ?
                  AND event_type = 'GALLERY_CALLED' AND created_at >= ?
                  AND gallery_name <> '统计'
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
                             THEN image_count ELSE 0 END) AS recent_calls
                FROM gallery_events
                WHERE platform_id = ? AND group_id = ?
                  AND gallery_name <> '统计'
                """,
                (cutoff, platform_id, group_id),
            ).fetchone()
        return {
            "contributors": [dict(row) for row in contributors],
            "popular": [dict(row) for row in popular],
            "additions": int(totals["additions"] or 0),
            "recent_calls": int(totals["recent_calls"] or 0),
        }
