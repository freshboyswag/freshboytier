import discord
from discord.ext import commands

from config import (
    ADMIN_PANEL_CHANNEL_ID, MANAGEMENT_ACCESS, QUOTA_RESET_ACCESS,
    PANEL_CHANNEL_ID, TICKETS_CHANNEL_ID, VACATION_CHANNEL_ID, DATABASE_CHANNEL_ID,
    TIER_ROLES, NO_TIER_CATEGORY_ID,
)
from database import (
    is_recruitment_open, set_recruitment_open,
    get_admin_panel_message_id, set_admin_panel_message_id,
    is_log_enabled, set_log_enabled,
    get_panel_override, set_panel_override,
    get_db, get_collection, get_tickets_collection, get_vacation_collection,
    get_category_pool, set_category_pool,
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

    embed = discord.Embed(
        title="⚙️ Админ-панель",
        description=(
            f"**Набор в семью:** {recruitment_text}\n\n"
            f"**Войс-логи:** {status('voice')}\n"
            f"**Логи входов/выходов:** {status('joins')}\n"
            f"**Логи ролей:** {status('roles')}\n"
            f"**Логи ника/имени:** {status('names')}\n"
            f"**Логи сообщений:** {status('messages')}\n"
            f"**Логи модерации (баны/кики):** {status('moderation')}"
        ),
        color=0x1ABC9C,  # цвет полоски больше не зависит от статуса набора
    )
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
# Подменю: Панели (отправить / редактировать)
# ───────────────────────────────────────────────

PANEL_CHOICES = [
    ("tickets", "📩 Заявки"),
    ("otpusk", "🏖️ Отпуска"),
    ("private", "🏠 Личные каналы"),
    ("database", "📋 База участников"),
]


class PanelsMenuView(discord.ui.View):
    """Первый экран: выбор между отправкой и редактированием панелей."""
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="📤 Отправить", style=discord.ButtonStyle.blurple)
    async def send_choice(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.edit_message(content="Какую панель отправить?", view=SendPanelView())

    @discord.ui.button(label="✏️ Редактировать", style=discord.ButtonStyle.blurple)
    async def edit_choice(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.edit_message(content="Текст какой панели редактируем?", view=EditPanelChoiceView())


class SendPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        for key, label in PANEL_CHOICES + [("all", "📦 Все панели")]:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.blurple,
                                     custom_id=f"admin_panel_send_{key}", row=0)
            btn.callback = self._make_callback(key)
            self.add_item(btn)

        back_btn = discord.ui.Button(label="⬅️ Назад", style=discord.ButtonStyle.secondary,
                                      custom_id="admin_panel_send_back", row=1)
        back_btn.callback = self._back
        self.add_item(back_btn)

    def _make_callback(self, key: str):
        async def callback(interaction: discord.Interaction):
            if not has_admin_panel_access(interaction.user):
                await interaction.response.send_message("нет прав", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            registry = get_panel_registry()
            targets = registry.keys() if key == "all" else [key]
            sent = []
            for k in targets:
                ok = await resend_panel(interaction.guild, interaction.client.user, k)
                if ok:
                    sent.append(k)
            await interaction.followup.send(
                f"отправлено: {', '.join(sent) if sent else 'ничего (проверь ID каналов)'}", ephemeral=True
            )
        return callback

    async def _back(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Что делаем с панелями?", view=PanelsMenuView())


class EditPanelChoiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        for key, label in PANEL_CHOICES:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.blurple,
                                     custom_id=f"admin_panel_edit_{key}", row=0)
            btn.callback = self._make_callback(key)
            self.add_item(btn)

        back_btn = discord.ui.Button(label="⬅️ Назад", style=discord.ButtonStyle.secondary,
                                      custom_id="admin_panel_edit_back", row=1)
        back_btn.callback = self._back
        self.add_item(back_btn)

    def _make_callback(self, key: str):
        async def callback(interaction: discord.Interaction):
            if not has_admin_panel_access(interaction.user):
                await interaction.response.send_message("нет прав", ephemeral=True)
                return
            registry = get_panel_registry()
            _, embed_fn, _, label = registry[key]
            current_embed = embed_fn()  # уже учитывает существующий оверрайд, если он был
            await interaction.response.send_modal(
                EditPanelModal(
                    panel_key=key,
                    panel_label=label,
                    current_title=current_embed.title,
                    current_description=current_embed.description,
                )
            )
        return callback

    async def _back(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Что делаем с панелями?", view=PanelsMenuView())


# ───────────────────────────────────────────────
# Подменю: Логи
# ───────────────────────────────────────────────

LOG_TYPES = [
    ("voice", "🔊 Войс-логи"),
    ("joins", "🚪 Входы/выходы"),
    ("roles", "🎭 Роли"),
    ("names", "✏️ Ник/имя"),
    ("messages", "📝 Сообщения"),
    ("moderation", "🔨 Модерация"),
]


class LogsToggleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        for i, (key, label) in enumerate(LOG_TYPES):
            enabled = is_log_enabled(key)
            btn = discord.ui.Button(
                label=f"{label}: {'Вкл' if enabled else 'Выкл'}",
                style=discord.ButtonStyle.green if enabled else discord.ButtonStyle.red,
                custom_id=f"admin_panel_log_toggle_{key}",
                row=i // 3,
            )
            btn.callback = self._make_callback(key, label)
            self.add_item(btn)

    def _make_callback(self, key: str, label: str):
        async def callback(interaction: discord.Interaction):
            if not has_admin_panel_access(interaction.user):
                await interaction.response.send_message("нет прав", ephemeral=True)
                return
            new_state = not is_log_enabled(key)
            set_log_enabled(key, new_state)
            await interaction.response.edit_message(view=LogsToggleView())
            await interaction.followup.send(
                f"✅ {label} {'включены' if new_state else 'выключены'}", ephemeral=True
            )
            await _refresh_main_panel(interaction)
        return callback


async def _refresh_main_panel(interaction: discord.Interaction):
    """Обновляет главное сообщение админ-панели, если изменения пришли из подменю."""
    try:
        channel = interaction.guild.get_channel(ADMIN_PANEL_CHANNEL_ID)
        if not channel:
            return
        msg_id = get_admin_panel_message_id()
        if not msg_id:
            return
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(embed=build_admin_panel_embed(), view=AdminPanelView())
    except Exception as e:
        print(f"[ERROR] обновление главной админ-панели: {e}")


# ───────────────────────────────────────────────
# Подменю: Общесерверные настройки (категории личных каналов)
# ───────────────────────────────────────────────

def _all_tier_keys_with_defaults(guild: discord.Guild):
    """[(tier_key, label, default_category_id), ...] для всех тиров + 'без тира'."""
    result = []
    for role_id, default_cat_id in TIER_ROLES.items():
        role = guild.get_role(role_id)
        label = role.name if role else f"роль {role_id}"
        result.append((str(role_id), label, default_cat_id))
    result.append(("no_tier", "Без тира", NO_TIER_CATEGORY_ID))
    return result


def build_categories_embed(guild: discord.Guild) -> discord.Embed:
    """Плоский список всех категорий личных каналов, отсортированный по позиции на сервере."""
    found = []   # (position, category_obj)
    missing = [] # cat_id без найденной категории

    for tier_key, _, default_cat_id in _all_tier_keys_with_defaults(guild):
        pool = get_category_pool(tier_key, default_category_id=default_cat_id)
        for cat_id in pool:
            category = guild.get_channel(int(cat_id))
            if category and isinstance(category, discord.CategoryChannel):
                found.append((category.position, category))
            else:
                missing.append(cat_id)

    found.sort(key=lambda pair: pair[0])

    lines = []
    for _, category in found:
        lines.append(f"`{category.name}` — {len(category.channels)}/50")
    for cat_id in missing:
        lines.append(f"`{cat_id}` — категория не найдена на сервере")

    if not lines:
        lines.append("_категории ещё не настроены_")

    embed = discord.Embed(
        title="🗂 Категории личных каналов",
        description="**Кол-во каналов в категориях:**\n" + "\n".join(lines),
        color=0x1ABC9C,
    )
    return embed


class EditCategoriesModal(discord.ui.Modal, title="Категории личных каналов"):
    def __init__(self, guild: discord.Guild):
        super().__init__()
        self.tier_fields = []  # [(tier_key, TextInput), ...]

        for tier_key, label, default_cat_id in _all_tier_keys_with_defaults(guild):
            pool = get_category_pool(tier_key, default_category_id=default_cat_id)
            field = discord.ui.TextInput(
                label=label[:45],
                style=discord.TextStyle.short,
                default=", ".join(pool),
                placeholder="ID категорий через запятую",
                required=False,
                max_length=400,
            )
            self.tier_fields.append((tier_key, field))
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        parsed = {}

        for tier_key, field in self.tier_fields:
            raw_ids = [x.strip() for x in field.value.split(",") if x.strip()]

            if not raw_ids:
                await interaction.response.send_message(
                    f"список категорий не может быть пустым (поле «{field.label}»)", ephemeral=True
                )
                return

            for cat_id in raw_ids:
                if not cat_id.isdigit():
                    await interaction.response.send_message(
                        f"`{cat_id}` — это не ID (нужны только цифры), поле «{field.label}»", ephemeral=True
                    )
                    return
                category = guild.get_channel(int(cat_id))
                if not category or not isinstance(category, discord.CategoryChannel):
                    await interaction.response.send_message(
                        f"категория с ID `{cat_id}` не найдена на сервере, поле «{field.label}»", ephemeral=True
                    )
                    return

            parsed[tier_key] = raw_ids

        for tier_key, ids in parsed.items():
            set_category_pool(tier_key, ids)

        await interaction.response.send_message("✅ категории обновлены", ephemeral=True)


class GeneralSettingsView(discord.ui.View):
    """Общесерверные настройки: редактирование ID категорий для личных каналов."""
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=120)
        self.guild = guild

    @discord.ui.button(label="✏️ Изменить категории", style=discord.ButtonStyle.blurple,
                        custom_id="admin_panel_cat_edit")
    async def edit_categories(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.send_modal(EditCategoriesModal(guild=self.guild))


# ───────────────────────────────────────────────
# View главной админ-панели
# ───────────────────────────────────────────────

class AdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self._build_items()

    def _build_items(self):
        open_now = is_recruitment_open()
        recruitment_btn = discord.ui.Button(
            label="Набор: Открыт" if open_now else "Набор: Закрыт",
            emoji="🟢" if open_now else "🔴",
            style=discord.ButtonStyle.green if open_now else discord.ButtonStyle.red,
            custom_id="admin_panel_recruitment_toggle",
            row=0,
        )
        recruitment_btn.callback = self.toggle_recruitment
        self.add_item(recruitment_btn)

        logs_btn = discord.ui.Button(label="📊 Логи", style=discord.ButtonStyle.secondary,
                                      custom_id="admin_panel_open_logs", row=0)
        logs_btn.callback = self.open_logs_menu
        self.add_item(logs_btn)

        panels_btn = discord.ui.Button(label="📑 Панели", style=discord.ButtonStyle.secondary,
                                        custom_id="admin_panel_open_panels", row=0)
        panels_btn.callback = self.open_panels_menu
        self.add_item(panels_btn)

        settings_btn = discord.ui.Button(label="⚙️ Общесерверные настройки", style=discord.ButtonStyle.secondary,
                                          custom_id="admin_panel_open_settings", row=0)
        settings_btn.callback = self.open_settings_menu
        self.add_item(settings_btn)

        quota_btn = discord.ui.Button(label="🔄 Сбросить квоту", style=discord.ButtonStyle.secondary,
                                       custom_id="admin_panel_quota_btn", row=1)
        quota_btn.callback = self.quota_reset
        self.add_item(quota_btn)

        stats_btn = discord.ui.Button(label="📈 Статистика", style=discord.ButtonStyle.secondary,
                                       custom_id="admin_panel_stats_btn", row=1)
        stats_btn.callback = self.show_stats
        self.add_item(stats_btn)

    async def toggle_recruitment(self, interaction: discord.Interaction):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав для управления панелью", ephemeral=True)
            return
        new_state = not is_recruitment_open()
        set_recruitment_open(new_state)
        await interaction.response.edit_message(embed=build_admin_panel_embed(), view=AdminPanelView())
        # пересобираем панель заявок, чтобы селект "Подать заявку" стал (не)активным
        await resend_panel(interaction.guild, interaction.client.user, "tickets")
        await interaction.followup.send(
            "✅ Набор в семью открыт" if new_state else "✅ Набор в семью закрыт", ephemeral=True
        )

    async def open_logs_menu(self, interaction: discord.Interaction):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.send_message("Переключи нужные логи:", view=LogsToggleView(), ephemeral=True)

    async def open_panels_menu(self, interaction: discord.Interaction):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.send_message(
            "Что делаем с панелями?", view=PanelsMenuView(), ephemeral=True
        )

    async def open_settings_menu(self, interaction: discord.Interaction):
        if not has_admin_panel_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_categories_embed(interaction.guild),
            view=GeneralSettingsView(guild=interaction.guild),
            ephemeral=True,
        )

    async def quota_reset(self, interaction: discord.Interaction):
        if not has_quota_reset_access(interaction.user):
            await interaction.response.send_message("нет прав для сброса квоты", ephemeral=True)
            return
        await interaction.response.send_modal(QuotaResetModal())

    async def show_stats(self, interaction: discord.Interaction):
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
        self._panel_posted_this_run = False
        bot.add_view(AdminPanelView())

    @commands.Cog.listener()
    async def on_ready(self):
        await self.ensure_panel_posted()

    async def ensure_panel_posted(self):
        if self._panel_posted_this_run:
            return  # уже пересоздана в этом запуске процесса — реконнекты гейтвея не дублируют

        channel = self.bot.get_channel(ADMIN_PANEL_CHANNEL_ID)
        if channel is None:
            print(f"[ADMIN PANEL] канал {ADMIN_PANEL_CHANNEL_ID} не найден")
            return

        # на каждом новом запуске/деплое панель пересоздаётся с нуля,
        # даже если старое сообщение всё ещё висит в канале
        async for message in channel.history(limit=100):
            if message.author == self.bot.user:
                await message.delete()

        msg = await channel.send(embed=build_admin_panel_embed(), view=AdminPanelView())
        set_admin_panel_message_id(msg.id)
        self._panel_posted_this_run = True


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminPanelCog(bot))
