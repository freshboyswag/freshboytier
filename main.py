import discord
from discord.ext import commands
import os
import asyncio
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()  # локально подтянет .env; на Render/сервере с уже заданными env-переменными — просто ничего не делает

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
        "cogs.logs",
        "cogs.reg",
        "cogs.vacation",
        "cogs.members",
        "cogs.profile",
        "cogs.admin_panel",
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

cogs_loaded = False

@bot.event
async def on_ready():
    global cogs_loaded
    if not cogs_loaded:
        await load_cogs()
        cogs_loaded = True

        # коги грузятся уже во время on_ready, поэтому их собственный on_ready
        # в этот первый раз не срабатывает — постим админ-панель явно
        admin_cog = bot.get_cog("AdminPanelCog")
        if admin_cog:
            await admin_cog.ensure_panel_posted()

    print(f"Бот запущен: {bot.user}")


# ───────────────────────────────────────────────
# Команды синхронизации
# ───────────────────────────────────────────────

@bot.tree.command(name="sync", description="Синхронизировать команды")
async def sync_slash(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("нет прав", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        bot.tree.copy_global_to(guild=interaction.guild)
        synced = await bot.tree.sync(guild=interaction.guild)
        await interaction.followup.send(f"синхронизировано {len(synced)} команд", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"ошибка: {e}", ephemeral=True)


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