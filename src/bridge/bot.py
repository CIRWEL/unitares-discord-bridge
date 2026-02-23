import asyncio
import discord
from discord.ext import commands
from bridge.config import DISCORD_TOKEN, GUILD_ID

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bridge online as {bot.user}")
    if GUILD_ID:
        guild = bot.get_guild(GUILD_ID)
        if guild:
            print(f"Connected to: {guild.name}")

def main():
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()
