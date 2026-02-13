import discord
from discord.ext import commands, tasks
import datetime
import asyncio
import pytz
import dotenv
import os
import re
import requests
import time
from bs4 import BeautifulSoup


# import DB helpers
from database import (
    init_db,
    get_all_games,
    get_message_id,
    save_message_id,
    add_reminder,
    get_due_reminders,
    delete_reminder,
    update_reminder_time,
    get_all_reminders
)

dotenv.load_dotenv()
tz = pytz.timezone("Europe/Berlin")

# reminder cache
disconnect_tasks = {}

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
TOKEN = os.getenv("TOKEN")

URL = "https://jwflab.com/en/warframe-prime-order/"
CACHE = {"data": None, "timestamp": 0}
CACHE_TTL = 3600  # 1 hour


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

def parse_reminder_time(input_str: str):
    now = datetime.datetime.now(pytz.timezone("Europe/Berlin"))

    # 1️⃣ Absolute date
    try:
        absolute = datetime.datetime.strptime(input_str, "%d/%m/%Y %H:%M")
        return pytz.timezone("Europe/Berlin").localize(absolute).astimezone(pytz.UTC)
    except ValueError:
        pass

    # 2️⃣ Relative time: e.g., 1d2h15m, 2h30m, 45m
    pattern = r"(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?"
    match = re.fullmatch(pattern, input_str.strip())
    if match:
        days = int(match.group("days") or 0)
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes") or 0)
        delta = datetime.timedelta(days=days, hours=hours, minutes=minutes)
        target_time = now + delta
        return target_time.astimezone(pytz.UTC)

    raise ValueError("Invalid time format")

def format_german_time(dt_utc: datetime.datetime):
    berlin = pytz.timezone("Europe/Berlin")
    dt_local = dt_utc.astimezone(berlin)
    today = datetime.datetime.now(berlin).date()
    tomorrow = today + datetime.timedelta(days=1)

    if dt_local.date() == today:
        day_str = "heute"
    elif dt_local.date() == tomorrow:
        day_str = "morgen"
    else:
        day_str = dt_local.strftime("%d/%m/%Y")  # fallback

    return f"{day_str} um {dt_local.strftime('%H:%M')} Uhr"

# warframe prime scraper
def fetch_prime_data():
    r = requests.get(URL, timeout=15)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    content = soup.select_one("#post-2010 .cm-entry-summary")
    if not content:
        raise Exception("Content container not found")

    # --- Find prediction table ---
    target_table = None
    for table in content.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if "Prime" in headers and "Scheduled Prime release" in headers:
            target_table = table
            break

    if not target_table:
        raise Exception("Prime schedule table not found")

    # --- Get confirmed prime safely ---
    confirmed = None

    # Collect paragraphs before table
    paragraphs = []
    for element in content.children:
        if element == target_table:
            break
        if getattr(element, "name", None) == "p":
            paragraphs.append(element)

    # Walk backwards from closest to table
    for p in reversed(paragraphs):
        text = p.get_text(strip=True)

        # Structural filter
        if "(" in text and ")" in text and "," in text:
            confirmed = text
            break

    # --- Get full predicted list ---
    results = []
    rows = target_table.find("tbody").find_all("tr")

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        prime_name = cols[1].get_text(strip=True)
        release_date = cols[2].get_text(strip=True)

        if prime_name and release_date:
            results.append((prime_name, release_date))

    return confirmed, results

# send prime schedule cached
def get_prime_schedule_cached():
    now = time.time()

    if CACHE["data"] and now - CACHE["timestamp"] < CACHE_TTL:
        return CACHE["data"]

    data = fetch_prime_data()
    CACHE["data"] = data
    CACHE["timestamp"] = now
    return data

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
    auto_update.start() # start game reset loop
    reminder_loop.start() # start reminder loop

    try:
        synced = await bot.tree.sync()
        print(f"🔗 Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"⚠️ Failed to sync: {e}")


# game reset loop (30 min)
@tasks.loop(minutes=30) 
async def auto_update():
    await update_or_create_messages()

# reminder loop (once every min check)
@tasks.loop(seconds=60)
async def reminder_loop():
    due = await get_due_reminders()
    for reminder in due:
        reminder_id, user_id, reason, remind_at, channel_id, recurring_interval = reminder
        channel = bot.get_channel(int(channel_id))
        if channel:

            remind_dt = datetime.datetime.fromisoformat(remind_at)
            if remind_dt.tzinfo is None:
                remind_dt = remind_dt.replace(tzinfo=pytz.UTC)

            embed = discord.Embed(
                title="⏰ Reminder",
                description=reason,
                color=discord.Color.blurple(),
                timestamp=remind_dt
            )
            embed.set_footer(text=f"made with UwU")
            
            # Mention user in content to actually ping
            await channel.send(content=f"<@{user_id}>", embed=embed)

        # Handle recurring reminders
        if recurring_interval:
            new_time = datetime.datetime.fromisoformat(remind_at) + datetime.timedelta(seconds=recurring_interval)
            await update_reminder_time(reminder_id, new_time)
        else:
            await delete_reminder(reminder_id)

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
            f"✅ Will disconnect everyone in voice at **{target_time.strftime('%H:%M')}**"
        )

        async def disconnect_task():
            await asyncio.sleep(delta)
            for vc in interaction.guild.voice_channels:
                for member in vc.members:
                    await member.move_to(None)
            #await interaction.followup.send("🔌 Everyone has been disconnected.")

        # Cancel previous one if exists
        if interaction.guild.id in disconnect_tasks:
            disconnect_tasks[interaction.guild.id].cancel()

        task = asyncio.create_task(disconnect_task())
        disconnect_tasks[interaction.guild.id] = task

    except ValueError:
        await interaction.response.send_message("❌ Invalid time format! Use HH:MM (24h).")

    except ValueError:
        await interaction.response.send_message("❌ Invalid time format! Use HH:MM (24h).")

@bot.tree.command(name="remind_me", description="Set a reminder")
async def remind_me(interaction: discord.Interaction, reason: str, time: str):
    try:
        # Parse time (absolute or relative)
        remind_time_utc = parse_reminder_time(time)

        # Save reminder in DB
        await add_reminder(
            str(interaction.user.id),
            reason,
            remind_time_utc,
            str(interaction.channel.id)
        )

        # Human-readable relative time
        delta = remind_time_utc - datetime.datetime.now(pytz.UTC)
        total_minutes = int(delta.total_seconds() // 60)
        days, remainder = divmod(total_minutes, 1440)  # 1440 = 24*60
        hours, minutes = divmod(remainder, 60)

        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        human_relative = "".join(parts)

        german_time = format_german_time(remind_time_utc)

        # Confirm to user
        await interaction.response.send_message(
            f"✅ Reminder set for **{german_time}** ({human_relative} from now): {reason}",
            ephemeral=True
        )

    except ValueError:
        await interaction.response.send_message(
            "❌ Invalid time format! Use either DD/MM/YYYY HH:MM or relative time like 1d2h30m.",
            ephemeral=True
        )

@bot.tree.command(name="reminders", description="List all current reminders")
async def reminders(interaction: discord.Interaction):
    all_reminders = await get_all_reminders()

    if not all_reminders:
        await interaction.response.send_message("📭 No reminders currently set.")
        return

    embed = discord.Embed(title="⏰ Current Reminders", color=discord.Color.blurple())

    for reminder in all_reminders:
        reminder_id, user_id, reason, remind_at, channel_id, recurring_interval = reminder
        remind_dt = datetime.datetime.fromisoformat(remind_at)
        if remind_dt.tzinfo is None:
            remind_dt = remind_dt.replace(tzinfo=pytz.UTC)

        german_time = format_german_time(remind_dt)
        recurring_text = f" (recurs every {recurring_interval}s)" if recurring_interval else ""
        embed.add_field(
            name=f"🆔 {reminder_id} — <@{user_id}> – {german_time}{recurring_text}",
            value=reason,
            inline=False
        )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="cancel_disconnect", description="Cancel the scheduled voice disconnect for this server")
async def cancel_disconnect(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id in disconnect_tasks:
        task = disconnect_tasks[guild_id]
        task.cancel()
        del disconnect_tasks[guild_id]
        await interaction.response.send_message("❌ Scheduled disconnect has been cancelled.")
    else:
        await interaction.response.send_message("ℹ️ No disconnect is currently scheduled.")

@bot.tree.command(name="cancel_reminder", description="Cancel one of your reminders")
async def cancel_reminder(interaction: discord.Interaction, reminder_id: int):
    all_reminders = await get_all_reminders()
    target = next((r for r in all_reminders if r[0] == reminder_id and str(r[1]) == str(interaction.user.id)), None)

    if not target:
        await interaction.response.send_message(
            "❌ Reminder not found or you don’t have permission to delete it.",
            ephemeral=True
        )
        return

    await delete_reminder(reminder_id)
    await interaction.response.send_message(
        f"🗑️ Reminder **{reminder_id}** has been cancelled.",
        ephemeral=True
    )

# warframe prime command
@bot.tree.command(name="prime_schedule", description="Shows the current Warframe Prime release order")
async def primes(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        confirmed, primes = get_prime_schedule_cached()

        embed = discord.Embed(
            title="Warframe Prime Release Schedule",
            color=0xE0C16F
        )

        # cOwOnfirmed Prime at top
        if confirmed:
            embed.add_field(
                name="Next Confirmed Prime Frame:",
                value=f"**{confirmed}**",
                inline=False
            )
        else:
            embed.add_field(
                name="Next Confirmed Prime Frame:",
                value="No confirmed Prime announced yet.",
                inline=False
            )

        # space
        #embed.add_field(name="\u200b", value="\u200b", inline=False)

        # full predicted list :3
        formatted_lines = [
            f"**{name} Prime** — {date}"
            for name, date in primes
        ]

        embed.add_field(
            name="Predicted Release Order",
            value="\n".join(formatted_lines),
            inline=False
        )

        embed.set_footer(text="Source: jwflab.com | Auto-updated hourly")

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(
            f"Error fetching data: {e}",
            ephemeral=True
        )

bot.run(TOKEN)
