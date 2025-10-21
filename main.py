import discord
from discord.ext import commands, tasks
import datetime
import asyncio
import pytz
import dotenv
import os
import re

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
            f"✅ will disconnect everyone in voice at **{target_time.strftime('%H:%M')}**"
        )

        await asyncio.sleep(delta)

        for guild in bot.guilds:
            for vc in guild.voice_channels:
                for member in vc.members:
                    await member.move_to(None)

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
            name=f"<@{user_id}> – {german_time}{recurring_text}",
            value=reason,
            inline=False
        )

    await interaction.response.send_message(embed=embed)

bot.run(TOKEN)
