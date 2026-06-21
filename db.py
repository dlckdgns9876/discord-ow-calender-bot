import aiosqlite

DB_PATH = "schedules.db"

_UPDATABLE = {"date", "title", "description", "start_time", "end_time"}


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                date        TEXT    NOT NULL,
                title       TEXT    NOT NULL,
                description TEXT,
                start_time  TEXT,
                end_time    TEXT,
                user_id     INTEGER,
                user_name   TEXT,
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for col in ("start_time TEXT", "end_time TEXT", "user_id INTEGER", "user_name TEXT"):
            try:
                await db.execute(f"ALTER TABLE schedules ADD COLUMN {col}")
            except Exception:
                pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS owcs_channels (
                guild_id   INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS owcs_notified (
                match_dt TEXT PRIMARY KEY
            )
        """)
        await db.commit()


# ── OWCS 알림 채널 ────────────────────────────────────────

async def set_owcs_channel(guild_id: int, channel_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO owcs_channels (guild_id, channel_id) VALUES (?, ?)",
            (guild_id, channel_id),
        )
        await db.commit()


async def get_all_owcs_channels() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT guild_id, channel_id FROM owcs_channels") as cur:
            return await cur.fetchall()


async def is_owcs_notified(match_dt: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM owcs_notified WHERE match_dt = ?", (match_dt,)
        ) as cur:
            return await cur.fetchone() is not None


async def mark_owcs_notified(match_dt: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO owcs_notified (match_dt) VALUES (?)", (match_dt,)
        )
        # 30일 이상 된 알림 기록 정리
        await db.execute(
            "DELETE FROM owcs_notified WHERE match_dt < datetime('now', '-30 days')"
        )
        await db.commit()


async def add_schedule(
    guild_id: int,
    date: str,
    title: str,
    description: str = None,
    start_time: str = None,
    end_time: str = None,
    user_id: int = None,
    user_name: str = None,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO schedules
               (guild_id, date, title, description, start_time, end_time, user_id, user_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, date, title, description, start_time, end_time, user_id, user_name),
        )
        await db.commit()


async def get_schedules_by_month(guild_id: int, year: int, month: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM schedules WHERE guild_id = ? AND date LIKE ? ORDER BY date, start_time",
            (guild_id, f"{year}-{month:02d}-%"),
        ) as cursor:
            return await cursor.fetchall()


async def get_schedules_by_date(guild_id: int, date: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM schedules WHERE guild_id = ? AND date = ? ORDER BY start_time",
            (guild_id, date),
        ) as cursor:
            return await cursor.fetchall()


async def update_schedule(
    schedule_id: int, guild_id: int, user_id: int, updates: dict
) -> str:
    """'ok' | 'not_found' | 'forbidden'"""
    if not updates:
        return "ok"
    if not updates.keys() <= _UPDATABLE:
        raise ValueError(f"허용되지 않은 필드: {updates.keys() - _UPDATABLE}")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id FROM schedules WHERE id = ? AND guild_id = ?",
            (schedule_id, guild_id),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return "not_found"
        # user_id가 NULL인 기존 데이터는 누구나 수정 가능
        if row["user_id"] is not None and row["user_id"] != user_id:
            return "forbidden"

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        await db.execute(
            f"UPDATE schedules SET {set_clause} WHERE id = ? AND guild_id = ?",
            [*updates.values(), schedule_id, guild_id],
        )
        await db.commit()
        return "ok"


async def delete_schedule(schedule_id: int, guild_id: int, user_id: int = None) -> str:
    """'ok' | 'not_found' | 'forbidden'"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id FROM schedules WHERE id = ? AND guild_id = ?",
            (schedule_id, guild_id),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return "not_found"
        if user_id is not None and row["user_id"] is not None and row["user_id"] != user_id:
            return "forbidden"

        await db.execute(
            "DELETE FROM schedules WHERE id = ? AND guild_id = ?",
            (schedule_id, guild_id),
        )
        await db.commit()
        return "ok"
