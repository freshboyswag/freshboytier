import discord
from discord.ext import commands
import os
import asyncio
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ───────────────────────────────────────────────
# Загрузка когов
# ───────────────────────────────────────────────

async def load_cogs():
    cogs = [
        "cogs.channels",
        "cogs.tickets",
        "cogs.voice_logs",
        "cogs.reg",
        "cogs.vacation",
        "cogs.member_logs",
        "cogs.members",
    ]
    for cog in cogs:
        try:
            await bot.load_extension(cog)
            print(f"[COG] загружен: {cog}")
        except Exception as e:
            print(f"[COG ERROR] {cog}: {e}")


# ───────────────────────────────────────────────
# События
# ───────────────────────────────────────────────

@bot.event
async def on_ready():
    await load_cogs()
    print(f"Бот запущен: {bot.user}")


# ───────────────────────────────────────────────
# Команды синхронизации
# ───────────────────────────────────────────────

@bot.tree.command(name="sync", description="Синхронизировать команды")
async def sync_slash(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("нет прав", ephemeral=True)
        return
    try:
        bot.tree.copy_global_to(guild=interaction.guild)
        synced = await bot.tree.sync(guild=interaction.guild)
        await interaction.response.send_message(f"синхронизировано {len(synced)} команд", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"ошибка: {e}", ephemeral=True)


@bot.command()
@commands.has_permissions(administrator=True)
async def sync(ctx):
    try:
        bot.tree.copy_global_to(guild=ctx.guild)
        synced = await bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"синхронизировано {len(synced)} команд")
    except Exception as e:
        await ctx.send(f"ошибка: {e}")


# ───────────────────────────────────────────────
# HTTP-сервер для Render (keepalive)
# ───────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def run_server():
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


Thread(target=run_server, daemon=True).start()
bot.run(os.getenv("TOKEN"))
