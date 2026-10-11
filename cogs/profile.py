import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View

from config import ARENA_SERVER_ID
from cogs.members import get_members_game_col, get_members_server_col, get_members_uid_counter, format_fullname, fmt_time
from majestic_api import get_arena_matches, find_player_arena_stats


def normalize_fullname_input(raw: str) -> str:
    """Любой формат ввода (Имя Фамилия / имя фамилия / Имя_Фамилия) -> имя_фамилия для БД."""
    raw = raw.strip().replace("_", " ")
    parts = [p for p in raw.split() if p]
    return "_".join(p.lower() for p in parts)


async def build_and_send_profile(interaction: discord.Interaction, target: discord.Member, doc: dict):
    static_raw = doc.get("static", "-")
    full_name = format_fullname(doc.get("full_name", "-"))

    embed = discord.Embed(title=f"Профиль — {target.display_name}", color=0x1ABC9C)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Discord", value=target.mention, inline=True)
    embed.add_field(name="Имя Фамилия", value=full_name, inline=True)
    embed.add_field(name="Статик", value=static_raw, inline=True)

    if not static_raw or static_raw == "-" or not str(static_raw).isdigit():
        embed.add_field(name="🎯 Арена", value="статик не указан — данные недоступны", inline=False)
        await interaction.followup.send(embed=embed)
        return

    static_id = int(static_raw)

    arena_data = await get_arena_matches(ARENA_SERVER_ID)
    if arena_data is None:
        embed.add_field(name="🎯 Арена", value="⚠️ не удалось получить данные (API недоступен)", inline=False)
        await interaction.followup.send(embed=embed)
        return

    stats = find_player_arena_stats(arena_data, static_id)

    if stats["total_matches"] == 0:
        embed.add_field(name="🎯 Арена", value="нет данных по последним матчам сервера", inline=False)
    else:
        embed.add_field(
            name="🎯 Арена — общая статистика",
            value=(
                f"Матчей: **{stats['total_matches']}**\n"
                f"Киллы / Смерти: **{stats['total_kills']}** / **{stats['total_deaths']}** (K/D {stats['kd']})\n"
                f"Заработано: **{stats['total_money']}**$"
            ),
            inline=False
        )

        if stats["recent"]:
            lines = []
            for m in stats["recent"]:
                kd_str = ""
                if m["kills"] is not None:
                    kd_str = f" — {m['kills']}/{m['death']} убийств/смертей, +{m['moneyWin']}$"
                lines.append(f"`#{m['id']}` {m['gamemode']} ({m['status']}){kd_str}")
            embed.add_field(name="Последние матчи", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"Данные арены сервера {ARENA_SERVER_ID} • из последних матчей")
    await interaction.followup.send(embed=embed)


# ───────────────────────────────────────────────
# ПРИВЯЗКА ДАННЫХ
# ───────────────────────────────────────────────

class BindProfileModal(Modal, title="Привязка игровых данных"):
    full_name = TextInput(
        label="Имя Фамилия",
        placeholder="Например: Иван Иванов",
        style=discord.TextStyle.short,
        min_length=3,
        max_length=100
    )
    static = TextInput(
        label="Статик",
        placeholder="например: 186404",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=20
    )

    def __init__(self, target: discord.Member):
        super().__init__()
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):
        static_value = self.static.value.strip()
        if not static_value.isdigit():
            await interaction.response.send_message("статик должен состоять только из цифр", ephemeral=True)
            return

        normalized_name = normalize_fullname_input(self.full_name.value)
        if "_" not in normalized_name:
            await interaction.response.send_message("укажи и имя, и фамилию", ephemeral=True)
            return

        await interaction.response.defer()

        col = get_members_game_col()
        discord_id = str(self.target.id)

        existing = col.find_one({"discord_id": discord_id})
        if existing:
            await interaction.followup.send("у тебя уже есть привязанные данные", ephemeral=True)
            return

        uid = get_members_uid_counter()
        doc = {
            "uid": uid,
            "discord_username": self.target.name,
            "discord_id": discord_id,
            "full_name": normalized_name,
            "static": static_value,
            "note": "-",
        }
        col.insert_one(doc)

        get_members_server_col().update_one(
            {"discord_id": discord_id},
            {"$set": {
                "self_bound": True,
                "self_bound_at": fmt_time(),
                "joined_server_at": self.target.joined_at.isoformat() if self.target.joined_at else None,
                "account_created_at": self.target.created_at.isoformat(),
            }},
            upsert=True
        )

        await build_and_send_profile(interaction, self.target, doc)


class BindPromptView(View):
    def __init__(self, target: discord.Member):
        super().__init__(timeout=120)
        self.target = target

    @discord.ui.button(label="🔗 Привязать данные", style=discord.ButtonStyle.blurple, custom_id="profile_bind")
    async def bind(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("это не твой профиль", ephemeral=True)
            return
        await interaction.response.send_modal(BindProfileModal(target=self.target))


# ───────────────────────────────────────────────
# КОГ
# ───────────────────────────────────────────────

class ProfileCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(name="profile", description="Игровой профиль участника (с данными арены)")
    async def profile(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        await interaction.response.defer()

        doc = get_members_game_col().find_one({"discord_id": str(target.id)})

        if not doc:
            if target.id == interaction.user.id:
                view = BindPromptView(target=target)
                await interaction.followup.send(
                    "❌ у тебя нет привязанных игровых данных.\n"
                    "Нажми кнопку ниже и укажи Имя Фамилию и Статик, чтобы профиль заработал.",
                    view=view
                )
            else:
                await interaction.followup.send(f"❌ {target.mention} не найден в базе участников", ephemeral=True)
            return

        await build_and_send_profile(interaction, target, doc)


async def setup(bot: commands.Bot):
    await bot.add_cog(ProfileCog(bot))
