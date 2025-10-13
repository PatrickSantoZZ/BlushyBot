# database.py
import aiosqlite

DB_PATH = "data.db"


async def init_db():
    """Initialize database and create tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                reset_hour INTEGER,
                tz TEXT,
                icon TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS game_resets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_name TEXT UNIQUE,
                message_id INTEGER,
                FOREIGN KEY(game_name) REFERENCES games(name)
            )
        """)
        await db.commit()


# -----------------------------
# GAME TABLE FUNCTIONS
# -----------------------------
async def get_all_games():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT name, reset_hour, tz, icon FROM games") as cursor:
            rows = await cursor.fetchall()
            return {row[0]: {"reset_hour": row[1], "tz": row[2], "icon": row[3]} for row in rows}


async def add_game(name: str, reset_hour: int, tz: str, icon: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO games (name, reset_hour, tz, icon) VALUES (?, ?, ?, ?)",
            (name, reset_hour, tz, icon or "")
        )
        await db.commit()


async def remove_game(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM games WHERE name = ?", (name,))
        await db.execute("DELETE FROM game_resets WHERE game_name = ?", (name,))
        await db.commit()


# -----------------------------
# GAME_RESETS TABLE FUNCTIONS
# -----------------------------
async def get_message_id(game_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT message_id FROM game_resets WHERE game_name = ?", (game_name,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def save_message_id(game_name: str, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO game_resets (game_name, message_id)
            VALUES (?, ?)
            ON CONFLICT(game_name)
            DO UPDATE SET message_id = excluded.message_id
        """, (game_name, message_id))
        await db.commit()
