import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View
import re

from config import (
    PANEL_CHANNEL_ID, TIER_ROLES, NO_TIER_CATEGORY_ID,
    REFRESH_ROLES, ADMIN_ROLE_ID
)
from database import get_collection


def has_refresh_role(member: discord.Member) -> bool:
    return any(role.id in REFRESH_ROLES for role in member.roles)

def get_category_for_member(member: discord.Member) -> int:
    member_role_ids = {role.id for role in member.roles}
    for role_id, category_id in TIER_ROLES.items():
        if role_id in member_role_ids:
            return category_id
    return NO_TIER_CATEGORY_ID


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

        category_id = get_category_for_member(interaction.user)
        category = interaction.guild.get_channel(category_id)

        if not category:
            await interaction.followup.send("категория не найдена, обратись к администратору", ephemeral=True)
            return

        name = self.channel_name.value.strip().replace(" ", "-").lower()
        admin_role = interaction.guild.get_role(ADMIN_ROLE_ID)
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel = await interaction.guild.create_text_channel(name, category=category, overwrites=overwrites)
        collection.insert_one({"user_id": user_id, "channel_id": channel.id})
        await interaction.followup.send(f"канал {channel.mention} создан!", ephemeral=True)


class PanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Создать личное дело", style=discord.ButtonStyle.blurple, custom_id="reg_channel", emoji="📁")
    async def reg_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelModal())


def build_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📁 Создание личного дела",
        description=(
            "Твой канал будет виден только тебе, людям с ролью <@&1510608237878181958> и админам.\n\n"
            "В нем должны быть твои откаты, которые ты после мп должен будешь залить на ютуб/рутуб, "
            "твои скрины и откаты с гг при необходимости. "
            "Тут же ты можешь задавать вопросы по скиллу"
        ),
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
