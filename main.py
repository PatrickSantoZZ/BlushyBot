import discord
from discord.ext import commands, tasks
import datetime
import pytz
import json
import dotenv
import os

dotenv.load_dotenv()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# files and "settings" stuff
Game_resets = "game_resets.json" # saved message ID's of send messaes
Tracked_games = "games.json" # the games we wanna track the reset timers of
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
TOKEN = os.getenv("TOKEN")

def load_ids():
    if os.path.exists(Game_resets):
        with open(Game_resets, "r") as f:
            return json.load(f)
    return {}

def save_ids(ids):
    with open(Game_resets, "w") as f:
        json.dump(ids, f, indent=2)

def load_games():
    if os.path.exists(Tracked_games):
        with open(Tracked_games, "r") as f:
            return json.load(f)
    return {}  # fallback

def get_next_reset(reset_hour, tz):
    tzinfo = pytz.timezone(tz)
    now = datetime.datetime.now(tzinfo)
    reset = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)

    if now >= reset:
        reset += datetime.timedelta(days=1)

    return int(reset.timestamp())

async def update_or_create_messages():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("⚠️ Channel not found")
        return

    ids = load_ids()
    GAMES = load_games()

    for game, info in GAMES.items():
        reset_timestamp = get_next_reset(info["reset_hour"], info["tz"])
        embed = discord.Embed(title=f"{game} Daily Reset", color=discord.Color.blurple())
        embed.add_field(
            name="Next Reset",
            value=f"<t:{reset_timestamp}:R> (<t:{reset_timestamp}:t>)",
            inline=False
        )
        # set image if available
        if "icon" in info and info["icon"]:
            embed.set_thumbnail(url=info["icon"])

        if game in ids: # memssage editing
            try:
                msg = await channel.fetch_message(ids[game])
                await msg.edit(embed=embed)
                print(f"✏️ Updated message for {game}")
            except discord.NotFound:
                msg = await channel.send(embed=embed)
                ids[game] = msg.id
                print(f"♻️ Recreated message for {game}")
        else:  
            msg = await channel.send(embed=embed)
            ids[game] = msg.id
            print(f"✅ Created message for {game}")

    save_ids(ids)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await bot.change_presence(activity=discord.Game(name="🐈 with my Kitty Timers uwu"))
    await update_or_create_messages()
    auto_update.start()

@tasks.loop(minutes=30)
async def auto_update():
    await update_or_create_messages()

bot.run(TOKEN)
