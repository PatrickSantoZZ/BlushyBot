import discord
from discord.ext import commands, tasks
import datetime
import asyncio
import pytz
import dotenv
import os

# import DB helpers
from database import (
    init_db,
    get_all_games,
    get_message_id,
    save_message_id
)

dotenv.load_dotenv()
tz = pytz.timezone("Europe/Berlin")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
TOKEN = os.getenv("TOKEN")


# -----------------------------
# Utility
# -----------------------------
def get_next_reset(reset_hour, tz):
    tzinfo = pytz.timezone(tz)
    now = datetime.datetime.now(tzinfo)
    reset = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    if now >= reset:
        reset += datetime.timedelta(days=1)
    return int(reset.timestamp())


# -----------------------------
# Core Bot Logic
# -----------------------------
async def update_or_create_messages():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("⚠️ Channel not found")
        return

    GAMES = await get_all_games()

    for game, info in GAMES.items():
        reset_timestamp = get_next_reset(info["reset_hour"], info["tz"])
        embed = discord.Embed(title=f"{game} Daily Reset", color=discord.Color.blurple())
        embed.add_field(
            name="Next Reset",
            value=f"<t:{reset_timestamp}:R> (<t:{reset_timestamp}:t>)",
            inline=False
        )
        if info["icon"]:
            embed.set_thumbnail(url=info["icon"])

        msg_id = await get_message_id(game)
        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(embed=embed)
                print(f"✏️ Updated message for {game}")
            except discord.NotFound:
                msg = await channel.send(embed=embed)
                await save_message_id(game, msg.id)
                print(f"♻️ Recreated message for {game}")
        else:
            msg = await channel.send(embed=embed)
            await save_message_id(game, msg.id)
            print(f"✅ Created message for {game}")
        
        await asyncio.sleep(5) # added cooldown of 5 sec since we got rate limited pretty hard (429)

    print("✅ Updated all messages")

# -----------------------------
# Events / Tasks
# -----------------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await init_db()
    await bot.change_presence(activity=discord.Game(name="🐈 with my Kitty Timers uwu"))
    auto_update.start()

    try:
        synced = await bot.tree.sync()
        print(f"🔗 Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"⚠️ Failed to sync: {e}")


@tasks.loop(minutes=30) # 30 min cd loop
async def auto_update():
    await update_or_create_messages()

# -----------------------------
# Slash Commands
# -----------------------------
@bot.tree.command(name="disconnect", description="Schedule a voice disconnect at a given time (HH:MM 24h, German time)")
async def disconnect(interaction: discord.Interaction, time: str):
    try:
        target_time = datetime.datetime.strptime(time, "%H:%M").time()
        now = datetime.datetime.now(tz)
        target_datetime = tz.localize(datetime.datetime.combine(now.date(), target_time))
        if target_datetime <= now:
            target_datetime += datetime.timedelta(days=1)
        delta = (target_datetime - now).total_seconds()

        await interaction.response.send_message(
            f"✅ will disconnect everyone in voice at **{target_time.strftime('%H:%M')}**"
        )

        await asyncio.sleep(delta)

        for guild in bot.guilds:
            for vc in guild.voice_channels:
                for member in vc.members:
                    await member.move_to(None)

    except ValueError:
        await interaction.response.send_message("❌ Invalid time format! Use HH:MM (24h).")


bot.run(TOKEN)
