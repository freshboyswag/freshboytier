import discord
from discord.ext import commands

from config import (
    ADMIN_PANEL_CHANNEL_ID, MANAGEMENT_ACCESS, QUOTA_RESET_ACCESS,
    PANEL_CHANNEL_ID, TICKETS_CHANNEL_ID, VACATION_CHANNEL_ID, DATABASE_CHANNEL_ID,
)
from database import (
    is_recruitment_open, set_recruitment_open,
    get_admin_panel_message_id, set_admin_panel_message_id,
    is_log_enabled, set_log_enabled,
    get_panel_override, set_panel_override,
    get_db, get_collection, get_tickets_collection, get_vacation_collection,
)


def has_admin_panel_access(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    role_ids = {r.id for r in member.roles}
    return bool(role_ids & MANAGEMENT_ACCESS)


def has_quota_reset_access(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    role_ids = {r.id for r in member.roles}
    return bool(role_ids & QUOTA_RESET_ACCESS)


def get_panel_registry():
    """Лениво импортирует, чтобы не ловить циклический импорт когов."""
    from cogs.channels import PanelView, build_panel_embed
    from cogs.tickets import TicketPanelView, build_ticket_panel_embed
    from cogs.vacation import VacationPanelView, build_vacation_panel_embed
    from cogs.members import DatabasePanelView, build_database_embed

    return {
        "private":  (PANEL_CHANNEL_ID, build_panel_embed, PanelView, "Панель личных каналов"),
        "tickets":  (TICKETS_CHANNEL_ID, build_ticket_panel_embed, TicketPanelView, "Панель заявок"),
        "otpusk":   (VACATION_CHANNEL_ID, build_vacation_panel_embed, VacationPanelView, "Панель отпусков"),
        "database": (DATABASE_CHANNEL_ID, build_database_embed, DatabasePanelView, "Панель базы участников"),
    }


async def resend_panel(guild: discord.Guild, bot_user: discord.ClientUser, panel_key: str) -> bool:
    """Удаляет старые сообщения бота в канале панели и постит свежий эмбед."""
    registry = get_panel_registry()
    channel_id, embed_fn, view_cls, _ = registry[panel_key]
    channel = guild.get_channel(channel_id)
    if not channel:
        return False

    async for message in channel.history(limit=100):
        if message.author == bot_user:
            await message.delete()

    await channel.send(embed=embed_fn(), view=view_cls())
    return True


# ───────────────────────────────────────────────
# Эмбед главной админ-панели
# ───────────────────────────────────────────────

def build_admin_panel_embed() -> discord.Embed:
    open_now = is_recruitment_open()

    def status(key):
        return "🟢 Включены" if is_log_enabled(key) else "🔴 Выключены"

    recruitment_text = "🟢 Открыт" if open_now else "🔴 Закрыт"
    color = 0x2ecc71 if open_now else 0xe74c3c

    embed = discord.Embed(
        title="⚙️ Админ-панель",
        description=(
            f"**Набор в семью:** {recruitment_text}\n\n"
            f"**Войс-логи:** {status('voice')}\n"
            f"**Логи входов/выходов:** {status('joins')}\n"
            f"**Логи ролей:** {status('roles')}\n"
            f"**Логи ника/имени:** {status('names')}\n"
            f"**Логи сообщений:** {status('messages')}\n"
            f"**Логи модерации (баны/кики):** {status('moderation')}\n\n"
            "Выбери действие в одном из меню ниже."
        ),
        color=color
    )
    embed.set_footer(text="Доступно только рекрутерам и администрации")
    return embed


# ───────────────────────────────────────────────
# Модалка редактирования текста панели
# ───────────────────────────────────────────────

class EditPanelModal(discord.ui.Modal):
    def __init__(self, panel_key: str, panel_label: str, current_title: str, current_description: str):
        super().__init__(title=f"Редактирование: {panel_label}"[:45])
        self.panel_key = panel_key

        self.title_input = discord.ui.TextInput(
            label="Заголовок панели",
            style=discord.TextStyle.short,
            default=current_title or "",
            max_length=256,
            required=True,
        )
        self.description_input = discord.ui.TextInput(
            label="Текст панели",
            style=discord.TextStyle.paragraph,
            default=current_description or "",
            max_length=4000,
            required=True,
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        set_panel_override(self.panel_key, self.title_input.value, self.description_input.value)

        ok = await resend_panel(interaction.guild, interaction.client.user, self.panel_key)

        if ok:
            await interaction.followup.send("✅ Панель обновлена и переотправлена в канал", ephemeral=True)
        else:
            await interaction.followup.send(
                "⚠️ Текст сохранён, но канал панели не найден — проверь ID канала в config.py",
                ephemeral=True
            )


# ───────────────────────────────────────────────
# View главной админ-панели
# ───────────────────────────────────────────────

class AdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ── Набор в семью ──
    @discord.ui.select(
        placeholder="Набор в семью...",
        custom_id="admin_panel_recruitment",
        options=[
            discord.SelectOption(label="Открыть набор", value="recruitment_open", emoji="🟢",
                                  description="Разрешить подавать заявки в семью"),
            discord.SelectOption(label="Закрыть набор", value="recruitment_close", emoji="🔴",
                                  description="Запретить подавать заявки в семью"),
        ]
    )
    async def select_recruitment(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав для управления панелью", ephemeral=True)
            return

        value = select.values[0]
        if value == "recruitment_open":
            set_recruitment_open(True)
            feedback = "✅ Набор в семью открыт"
        else:
            set_recruitment_open(False)
            feedback = "✅ Набор в семью закрыт"

        await interaction.response.edit_message(embed=build_admin_panel_embed(), view=self)
        await interaction.followup.send(feedback, ephemeral=True)

    # ── Логи ──
    @discord.ui.select(
        placeholder="Логи...",
        custom_id="admin_panel_logs",
        options=[
            discord.SelectOption(label="Войс-логи: переключить", value="toggle_voice", emoji="🔊",
                                  description="Вкл/выкл логи войса (джоины/муты/дисконнекты)"),
            discord.SelectOption(label="Логи входов/выходов: переключить", value="toggle_joins", emoji="🚪",
                                  description="Вкл/выкл логи входа и выхода участников с сервера"),
            discord.SelectOption(label="Логи ролей: переключить", value="toggle_roles", emoji="🎭",
                                  description="Вкл/выкл логи выдачи/снятия ролей"),
            discord.SelectOption(label="Логи ника/имени: переключить", value="toggle_names", emoji="✏️",
                                  description="Вкл/выкл логи смены ника и юзернейма"),
            discord.SelectOption(label="Логи сообщений: переключить", value="toggle_messages", emoji="📝",
                                  description="Вкл/выкл логи редактирования/удаления сообщений"),
            discord.SelectOption(label="Логи модерации: переключить", value="toggle_moderation", emoji="🔨",
                                  description="Вкл/выкл логи банов/киков"),
        ]
    )
    async def select_logs(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав для управления панелью", ephemeral=True)
            return

        labels = {
            "toggle_voice": ("voice", "Войс-логи"),
            "toggle_joins": ("joins", "Логи входов/выходов"),
            "toggle_roles": ("roles", "Логи ролей"),
            "toggle_names": ("names", "Логи ника/имени"),
            "toggle_messages": ("messages", "Логи сообщений"),
            "toggle_moderation": ("moderation", "Логи модерации"),
        }

        value = select.values[0]
        if value in labels:
            log_key, log_label = labels[value]
            new_state = not is_log_enabled(log_key)
            set_log_enabled(log_key, new_state)
            feedback = f"✅ {log_label} {'включены' if new_state else 'выключены'}"
        else:
            feedback = "неизвестное действие"

        await interaction.response.edit_message(embed=build_admin_panel_embed(), view=self)
        await interaction.followup.send(feedback, ephemeral=True)

    # ── Переотправка панелей ──
    @discord.ui.select(
        placeholder="Отправить панель...",
        custom_id="admin_panel_resend",
        options=[
            discord.SelectOption(label="Панель заявок", value="tickets", emoji="📩"),
            discord.SelectOption(label="Панель отпусков", value="otpusk", emoji="🏖️"),
            discord.SelectOption(label="Панель личных каналов", value="private", emoji="🏠"),
            discord.SelectOption(label="Панель базы участников", value="database", emoji="📋"),
            discord.SelectOption(label="Все панели", value="all", emoji="📦"),
        ]
    )
    async def select_resend(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав для управления панелью", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        value = select.values[0]
        registry = get_panel_registry()
        targets = registry.keys() if value == "all" else [value]

        sent = []
        for key in targets:
            ok = await resend_panel(interaction.guild, interaction.client.user, key)
            if ok:
                sent.append(key)

        await interaction.followup.send(
            f"отправлено: {', '.join(sent) if sent else 'ничего (проверь ID каналов)'}",
            ephemeral=True
        )

    # ── Редактирование текста панелей ──
    @discord.ui.select(
        placeholder="Редактировать текст панели...",
        custom_id="admin_panel_edit",
        options=[
            discord.SelectOption(label="Панель заявок", value="tickets", emoji="📩"),
            discord.SelectOption(label="Панель отпусков", value="otpusk", emoji="🏖️"),
            discord.SelectOption(label="Панель личных каналов", value="private", emoji="🏠"),
            discord.SelectOption(label="Панель базы участников", value="database", emoji="📋"),
        ]
    )
    async def select_edit(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав для управления панелью", ephemeral=True)
            return

        panel_key = select.values[0]
        registry = get_panel_registry()
        _, embed_fn, _, label = registry[panel_key]

        current_embed = embed_fn()  # уже учитывает существующий оверрайд, если он был

        await interaction.response.send_modal(
            EditPanelModal(
                panel_key=panel_key,
                panel_label=label,
                current_title=current_embed.title,
                current_description=current_embed.description,
            )
        )

    # ── Сброс квоты + статистика ──
    @discord.ui.button(label="Сбросить квоту участнику", style=discord.ButtonStyle.secondary,
                        emoji="🔄", custom_id="admin_panel_quota_btn")
    async def quota_reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_quota_reset_access(interaction.user):
            await interaction.response.send_message("нет прав для сброса квоты", ephemeral=True)
            return
        await interaction.response.send_modal(QuotaResetModal())

    @discord.ui.button(label="Статистика", style=discord.ButtonStyle.secondary,
                        emoji="📊", custom_id="admin_panel_stats_btn")
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав для просмотра статистики", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        embed = build_stats_embed(interaction.guild)
        await interaction.followup.send(embed=embed, ephemeral=True)


# ───────────────────────────────────────────────
# Модалка сброса квоты личного канала
# ───────────────────────────────────────────────

class QuotaResetModal(discord.ui.Modal, title="Сброс квоты личного канала"):
    user_input = discord.ui.TextInput(
        label="Юзернейм или айди",
        style=discord.TextStyle.short,
        placeholder="",
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        import re
        match = re.search(r"\d{17,20}", self.user_input.value)
        if not match:
            await interaction.response.send_message("не удалось найти ID в введённом тексте", ephemeral=True)
            return

        user_id = match.group()
        try:
            collection = get_collection()
            result = collection.delete_one({"user_id": user_id})
        except Exception as e:
            await interaction.response.send_message("ошибка базы данных", ephemeral=True)
            print(f"[ERROR] admin_panel quota reset: {e}")
            return

        if result.deleted_count == 0:
            await interaction.response.send_message("этот участник ещё не создавал личный канал", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ квота сброшена для <@{user_id}>", ephemeral=True)


# ───────────────────────────────────────────────
# Статистика по серверу
# ───────────────────────────────────────────────

def build_stats_embed(guild: discord.Guild) -> discord.Embed:
    try:
        db = get_db()
        members_count = db["members_game"].count_documents({})
        counters_doc = {doc["_id"]: doc.get("value") for doc in db["counters"].find({})}
        mp_counter = counters_doc.get("mp_counter", "—")
        uid_counter = counters_doc.get("members_uid_counter", "—")
    except Exception as e:
        members_count, mp_counter, uid_counter = "ошибка", "ошибка", "ошибка"
        print(f"[ERROR] stats members/counters: {e}")

    try:
        open_tickets = get_tickets_collection().count_documents({})
    except Exception:
        open_tickets = "ошибка"

    try:
        pending_vacations = get_vacation_collection().count_documents({})
    except Exception:
        pending_vacations = "ошибка"

    try:
        personal_channels = get_collection().count_documents({})
    except Exception:
        personal_channels = "ошибка"

    embed = discord.Embed(title="📊 Статистика сервера", color=0x3498db)
    embed.add_field(name="Участников на сервере", value=str(guild.member_count), inline=True)
    embed.add_field(name="Участников в базе", value=str(members_count), inline=True)
    embed.add_field(name="Личных каналов создано", value=str(personal_channels), inline=True)
    embed.add_field(name="Открытых заявок", value=str(open_tickets), inline=True)
    embed.add_field(name="Заявок на отпуск в ожидании", value=str(pending_vacations), inline=True)
    embed.add_field(name="Счётчик МП", value=str(mp_counter), inline=True)
    embed.add_field(name="Счётчик записей базы (uid)", value=str(uid_counter), inline=True)
    embed.timestamp = discord.utils.utcnow()
    return embed


# ───────────────────────────────────────────────
# Ког
# ───────────────────────────────────────────────

class AdminPanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(AdminPanelView())

    @commands.Cog.listener()
    async def on_ready(self):
        await self.ensure_panel_posted()

    async def ensure_panel_posted(self):
        channel = self.bot.get_channel(ADMIN_PANEL_CHANNEL_ID)
        if channel is None:
            print(f"[ADMIN PANEL] канал {ADMIN_PANEL_CHANNEL_ID} не найден")
            return

        existing_id = get_admin_panel_message_id()
        if existing_id:
            try:
                await channel.fetch_message(int(existing_id))
                return  # панель уже существует и доступна, ничего не постим заново
            except (discord.NotFound, discord.HTTPException):
                pass  # сообщение удалили — запостим новое ниже

        msg = await channel.send(embed=build_admin_panel_embed(), view=AdminPanelView())
        set_admin_panel_message_id(msg.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminPanelCog(bot))
