# migrate_json_to_db.py

import json
import asyncio
import os
import aiosqlite
from database import init_db, add_game, save_message_id

# paths to your old JSON files
GAMES_JSON = "games.json"
RESETS_JSON = "game_resets.json"


async def migrate():
    # 1️⃣ Initialize database and create tables
    await init_db()

    # 2️⃣ Load JSON files
    if not os.path.exists(GAMES_JSON):
        print(f"❌ {GAMES_JSON} not found!")
        return
    if not os.path.exists(RESETS_JSON):
        print(f"⚠️ {RESETS_JSON} not found — skipping message IDs")

    with open(GAMES_JSON, "r") as f:
        games_data = json.load(f)

    with open(RESETS_JSON, "r") as f:
        resets_data = json.load(f)

    # 3️⃣ Insert games into DB
    print("📥 Inserting games...")
    for name, info in games_data.items():
        await add_game(
            name,
            info.get("reset_hour", 0),
            info.get("tz", "Etc/GMT"),
            info.get("icon", "")
        )
    print(f"✅ Imported {len(games_data)} games")

    # 4️⃣ Insert game reset message IDs
    if resets_data:
        print("📨 Inserting message IDs...")
        for game_name, msg_id in resets_data.items():
            await save_message_id(game_name, msg_id)
        print(f"✅ Imported {len(resets_data)} message IDs")

    print("🎉 Migration complete!")


if __name__ == "__main__":
    asyncio.run(migrate())

async def add_channel_column():
    async with aiosqlite.connect("data.db") as db:
        await db.execute("ALTER TABLE reminders ADD COLUMN channel_id TEXT")
        await db.commit()
        print("✅ Added channel_id column")

#asyncio.run(add_channel_column())