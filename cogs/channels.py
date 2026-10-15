import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View
import re

from config import (
    PANEL_CHANNEL_ID, TIER_ROLES, NO_TIER_CATEGORY_ID,
    REFRESH_ROLES, TIER_CHECK_ROLE_ID
)
from database import get_collection, get_category_pool, add_category_to_pool


def has_refresh_role(member: discord.Member) -> bool:
    return any(role.id in REFRESH_ROLES for role in member.roles)

def get_tier_key(member: discord.Member) -> str:
    """Ключ пула категорий для участника: ID роли тира или 'no_tier'."""
    member_role_ids = {role.id for role in member.roles}
    for role_id in TIER_ROLES:
        if role_id in member_role_ids:
            return str(role_id)
    return "no_tier"


async def get_or_create_category(guild: discord.Guild, member: discord.Member):
    """
    Находит свободную (<50 каналов) категорию из пула тира участника.
    Пул изначально = одна категория из config.py (TIER_ROLES/NO_TIER_CATEGORY_ID),
    дальше пополняется вручную через админ-панель ("⚙️ Общесерверные настройки" → категории).
    Если все категории пула заполнены — бот автоматически создаёт новую с суффиксом
    "#2"/"#3"/... по имени последней категории пула и сам добавляет её в пул.
    """
    tier_key = get_tier_key(member)
    default_cat_id = TIER_ROLES.get(int(tier_key)) if tier_key != "no_tier" else NO_TIER_CATEGORY_ID

    pool = get_category_pool(tier_key, default_category_id=default_cat_id)

    base_name = None
    for cat_id in pool:
        category = guild.get_channel(int(cat_id))
        if category and isinstance(category, discord.CategoryChannel):
            base_name = category.name
            if len(category.channels) < 50:
                return category

    # весь пул заполнен (или пуст, но дефолтная категория не найдена) — создаём новую сами
    try:
        name_root = re.sub(r"\s*#\d+$", "", base_name) if base_name else f"личные каналы ({tier_key})"
        existing_names = {c.name for c in guild.categories}
        counter = 2
        candidate = f"{name_root} #{counter}"
        while candidate in existing_names:
            counter += 1
            candidate = f"{name_root} #{counter}"
        new_category = await guild.create_category(candidate)
        add_category_to_pool(tier_key, str(new_category.id))
        print(f"[CATEGORY] лимит категории исчерпан, создана '{candidate}' для {tier_key}")
        return new_category
    except Exception as e:
        print(f"[ERROR] авто-создание категории для {tier_key}: {e}")
        return None


# ───────────────────────────────────────────────
# UI
# ───────────────────────────────────────────────

class ChannelModal(Modal, title="Создать личное дело"):
    channel_name = TextInput(
        label="Название",
        placeholder="ник в игре",
        min_length=1,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)

        try:
            collection = get_collection()
            existing_entry = collection.find_one({"user_id": user_id})
        except Exception as e:
            await interaction.followup.send("ошибка базы данных", ephemeral=True)
            print(f"[ERROR] MongoDB: {e}")
            return

        if existing_entry:
            await interaction.followup.send("у тебя уже есть канал или квота израсходована", ephemeral=True)
            return

        category = await get_or_create_category(interaction.guild, interaction.user)

        if not category:
            await interaction.followup.send(
                "не удалось найти или создать категорию, обратись к администратору", ephemeral=True
            )
            return

        name = self.channel_name.value.strip().replace(" ", "-").lower()
        tier_check_role = interaction.guild.get_role(TIER_CHECK_ROLE_ID)
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True),
        }
        if tier_check_role:
            overwrites[tier_check_role] = discord.PermissionOverwrite(view_channel=True)

        channel = await interaction.guild.create_text_channel(name, category=category, overwrites=overwrites)
        collection.insert_one({"user_id": user_id, "channel_id": channel.id})

        # TODO: заглушка, придумать нормальный текст приветствия в личном канале
        await channel.send(f"{interaction.user.mention} добро пожаловать в твой личный канал")

        await interaction.followup.send(f"канал {channel.mention} создан!", ephemeral=True)


class PanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Создать личное дело", style=discord.ButtonStyle.blurple, custom_id="reg_channel", emoji="📁")
    async def reg_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelModal())


def build_panel_embed() -> discord.Embed:
    from database import get_panel_override
    override = get_panel_override("private")

    default_title = "📁 Создание личного дела"
    default_description = (
        "Твой канал будет виден только тебе, людям с ролью <@&1510608237878181958> и админам.\n\n"
        "В нем должны быть твои откаты, которые ты после мп должен будешь залить на ютуб/рутуб, "
        "твои скрины и откаты с гг при необходимости. "
        "Тут же ты можешь задавать вопросы по скиллу"
    )

    embed = discord.Embed(
        title=override["title"] if override else default_title,
        description=override["description"] if override else default_description,
        color=0x1ABC9C
    )
    embed.set_image(url="https://cdn.discordapp.com/attachments/1510631159414128700/1510639319969304646/0531.gif")
    return embed


# ───────────────────────────────────────────────
# Ког
# ───────────────────────────────────────────────

class ChannelsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(PanelView())

    @discord.app_commands.command(name="panels", description="Отправить панель в нужный канал")
    async def panels(self, interaction: discord.Interaction, panel: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        allowed = ["tickets", "otpusk", "private", "database", "all"]
        if panel not in allowed:
            await interaction.response.send_message(
                f"неизвестная панель. доступные: {', '.join(allowed)}", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        sent = []

        # Импортируем нужные вещи из других когов
        from cogs.tickets import TicketPanelView, build_ticket_panel_embed, TICKETS_CHANNEL_ID
        from cogs.vacation import VacationPanelView, build_vacation_panel_embed, VACATION_CHANNEL_ID
        from cogs.members import DatabasePanelView, build_database_embed, DATABASE_CHANNEL_ID

        async def send_panel(channel_id, embed_fn, view_fn):
            ch = interaction.guild.get_channel(channel_id)
            if not ch:
                return False
            async for message in ch.history(limit=100):
                if message.author == self.bot.user:
                    await message.delete()
            await ch.send(embed=embed_fn(), view=view_fn())
            return True

        if panel in ("private", "all"):
            ok = await send_panel(PANEL_CHANNEL_ID, build_panel_embed, PanelView)
            if ok:
                sent.append("private")

        if panel in ("tickets", "all"):
            ok = await send_panel(TICKETS_CHANNEL_ID, build_ticket_panel_embed, TicketPanelView)
            if ok:
                sent.append("tickets")

        if panel in ("otpusk", "all"):
            ok = await send_panel(VACATION_CHANNEL_ID, build_vacation_panel_embed, VacationPanelView)
            if ok:
                sent.append("otpusk")

        if panel in ("database", "all"):
            ok = await send_panel(DATABASE_CHANNEL_ID, build_database_embed, DatabasePanelView)
            if ok:
                sent.append("database")

        await interaction.followup.send(
            f"отправлено: {', '.join(sent) if sent else 'ничего'}", ephemeral=True
        )

    @discord.app_commands.command(name="refresh", description="Сбросить квоту на создание канала")
    async def refresh(self, interaction: discord.Interaction, user: str):
        if not has_refresh_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        match = re.search(r"\d{17,20}", user)
        if not match:
            await interaction.response.send_message("укажи тег или айди пользователя", ephemeral=True)
            return

        user_id = match.group()
        try:
            collection = get_collection()
            result = collection.delete_one({"user_id": user_id})
        except Exception as e:
            await interaction.response.send_message("ошибка базы данных", ephemeral=True)
            print(f"[ERROR] MongoDB: {e}")
            return

        if result.deleted_count == 0:
            await interaction.response.send_message("он ещё не регал", ephemeral=True)
            return

        await interaction.response.send_message("квота на создание личного канала сброшена", ephemeral=True)

    @discord.app_commands.command(name="logs", description="Включить или выключить логи")
    async def logs_cmd(self, interaction: discord.Interaction, type: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        from database import is_log_enabled, set_log_enabled
        allowed = ["voice", "joins"]
        if type not in allowed:
            await interaction.followup.send(
                f"неизвестный тип. доступные: {', '.join(allowed)}", ephemeral=True
            )
            return

        current = is_log_enabled(type)
        set_log_enabled(type, not current)
        status = "включены ✅" if not current else "выключены ❌"
        await interaction.followup.send(f"логи `{type}` {status}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelsCog(bot))
