import uuid

import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View

from config import ADMIN_PANEL_CHANNEL_ID
from database import (
    get_ticket_settings, update_ticket_settings,
    is_recruitment_open, set_recruitment_open,
    get_tickets_collection,
)
from cogs.tickets import has_ticket_access, TicketPanelView, build_ticket_panel_embed


def ro(ids):
    """role ids -> ролевые упоминания для текста"""
    return ", ".join(f"<@&{i}>" for i in ids) if ids else "—"


def co(ids):
    return ", ".join(f"<#{i}>" for i in ids) if ids else "—"


# ───────────────────────────────────────────────
# Пересылка панели заявок в её канал
# ───────────────────────────────────────────────

async def resend_ticket_panel(guild: discord.Guild, bot_user: discord.ClientUser) -> bool:
    settings = get_ticket_settings()
    channel = guild.get_channel(settings["channel_tickets"])
    if not channel:
        return False

    async for message in channel.history(limit=100):
        if message.author == bot_user:
            await message.delete()

    content_embed = build_ticket_panel_embed()
    image_url = settings.get("panel_image_url")

    embeds = []
    if image_url:
        banner_embed = discord.Embed(color=content_embed.color)
        banner_embed.set_image(url=image_url)
        embeds.append(banner_embed)
    embeds.append(content_embed)

    await channel.send(embeds=embeds, view=TicketPanelView())
    return True


# ───────────────────────────────────────────────
# Главный эмбед админ-панели
# ───────────────────────────────────────────────

def build_admin_panel_embed(guild: discord.Guild) -> discord.Embed:
    settings = get_ticket_settings()
    open_now = is_recruitment_open()

    try:
        open_tickets = get_tickets_collection().count_documents({})
    except Exception:
        open_tickets = "—"

    embed = discord.Embed(
        title="⚙️ Админ-панель заявок",
        description=(
            f"## Набор в семью: {'🟢 Открыт' if open_now else '🔴 Закрыт'}\n"
            f"Открытых заявок сейчас: **{open_tickets}**"
        ),
        color=0x57F287 if open_now else 0xED4245,
    )
    embed.add_field(name="📩 Канал заявки", value=co([settings["channel_tickets"]]), inline=True)
    embed.add_field(name="🗂️ Категория тикетов", value=co([settings["channel_category"]]), inline=True)
    embed.add_field(name="📨 Результаты", value=co([settings["channel_results"]]), inline=True)
    embed.add_field(name="✅ Лог принятых", value=co([settings["channel_accepted_logs"]]), inline=True)
    embed.add_field(name="❌ Лог отклонённых", value=co([settings["channel_rejected_logs"]]), inline=True)
    embed.add_field(name="🎙️ Канал обзвона", value=co([settings["channel_call"]]), inline=True)
    embed.add_field(name="📣 Роли для пинга", value=ro(settings["ping_role_ids"]), inline=False)
    embed.add_field(name="🔑 Роли с доступом", value=ro(settings["access_role_ids"]), inline=False)
    embed.set_footer(text=f"Полей в форме заявки: {len(settings.get('fields', []))}/5")
    return embed


# ═══════════════════════════════════════════════
# Редактирование текста/картинки/канала панели заявок
# ═══════════════════════════════════════════════

class EditPanelModal(Modal, title="Текст панели заявок"):
    def __init__(self, settings: dict):
        super().__init__()
        self.title_input = TextInput(
            label="Заголовок панели",
            style=discord.TextStyle.short,
            default=settings.get("panel_title") or "",
            max_length=256,
            required=True,
        )
        self.description_input = TextInput(
            label="Текст панели",
            style=discord.TextStyle.paragraph,
            default=settings.get("panel_description") or "",
            max_length=4000,
            required=True,
        )
        self.image_url_input = TextInput(
            label="Ссылка на картинку (баннер сверху)",
            style=discord.TextStyle.short,
            default=settings.get("panel_image_url") or "",
            placeholder="пусто — без картинки",
            max_length=500,
            required=False,
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)
        self.add_item(self.image_url_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        image_url = self.image_url_input.value.strip() or None
        if image_url and not image_url.startswith(("http://", "https://")):
            await interaction.followup.send("ссылка на картинку должна начинаться с http:// или https://", ephemeral=True)
            return

        update_ticket_settings(
            panel_title=self.title_input.value,
            panel_description=self.description_input.value,
            panel_image_url=image_url,
        )
        ok = await resend_ticket_panel(interaction.guild, interaction.client.user)
        if ok:
            await interaction.followup.send("✅ Панель заявок обновлена и переотправлена в канал", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Текст сохранён, но канал панели не найден — укажи его селектом ниже", ephemeral=True)


class PanelChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, current_id: int):
        super().__init__(
            placeholder="Канал, где висит панель 'Подать заявку'",
            channel_types=[discord.ChannelType.text],
            default_values=[discord.Object(id=current_id, type=discord.TextChannel)] if current_id else [],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        update_ticket_settings(channel_tickets=self.values[0].id)
        await interaction.response.send_message(
            f"✅ канал панели заявок теперь {self.values[0].mention}. Нажми «Переслать заново», чтобы запостить её там.",
            ephemeral=True,
        )


class PanelMenuView(View):
    def __init__(self, settings: dict):
        super().__init__(timeout=180)
        self.add_item(PanelChannelSelect(settings.get("channel_tickets")))

    @discord.ui.button(label="✏️ Изменить текст и картинку", style=discord.ButtonStyle.blurple, row=1)
    async def edit_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = get_ticket_settings()
        await interaction.response.send_modal(EditPanelModal(settings))

    @discord.ui.button(label="📤 Переслать заново", style=discord.ButtonStyle.secondary, row=1)
    async def resend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        ok = await resend_ticket_panel(interaction.guild, interaction.client.user)
        await interaction.followup.send("✅ переслано" if ok else "⚠️ канал панели не найден", ephemeral=True)


# ═══════════════════════════════════════════════
# Конструктор полей заявки
# ═══════════════════════════════════════════════

def _field_summary(field: dict) -> str:
    kind = "многострочное" if field.get("style") == "paragraph" else "однострочное"
    req = "обязательное" if field.get("required", True) else "необязательное"
    return (
        f"**{field['label']}**\n"
        f"placeholder: `{field.get('placeholder') or '—'}` · {kind}, {req}, лимит {field.get('max_length', 100)} симв."
    )


def build_fields_embed(settings: dict) -> discord.Embed:
    embed = discord.Embed(title="📝 Поля заявки", color=0x2b2d31,
                           description="Поля, которые видит пользователь при подаче заявки (макс. 5 — ограничение Discord).")
    fields = settings.get("fields", [])
    if not fields:
        embed.add_field(name="пусто", value="полей пока нет", inline=False)
    for i, field in enumerate(fields, start=1):
        embed.add_field(name=f"{i}.", value=_field_summary(field), inline=False)
    return embed


class AddFieldModal(Modal, title="Новое поле заявки"):
    def __init__(self, style: str):
        super().__init__()
        self.style = style
        self.label_input = TextInput(label="Заголовок поля", style=discord.TextStyle.short, max_length=45, required=True)
        self.placeholder_input = TextInput(label="Пример (placeholder)", style=discord.TextStyle.short, max_length=100, required=False)
        self.max_length_input = TextInput(
            label="Лимит символов (число)", style=discord.TextStyle.short, required=False,
            default="1000" if style == "paragraph" else "100",
        )
        self.add_item(self.label_input)
        self.add_item(self.placeholder_input)
        self.add_item(self.max_length_input)

    async def on_submit(self, interaction: discord.Interaction):
        settings = get_ticket_settings()
        fields = settings.get("fields", [])
        if len(fields) >= 5:
            await interaction.response.send_message("⚠️ больше 5 полей нельзя — это лимит модалок Discord", ephemeral=True)
            return

        try:
            max_length = int(self.max_length_input.value) if self.max_length_input.value.strip() else (1000 if self.style == "paragraph" else 100)
            max_length = max(1, min(max_length, 1024))
        except ValueError:
            max_length = 1000 if self.style == "paragraph" else 100

        fields.append({
            "id": uuid.uuid4().hex[:8],
            "label": self.label_input.value,
            "placeholder": self.placeholder_input.value or "",
            "style": self.style,
            "max_length": max_length,
            "required": True,
        })
        update_ticket_settings(fields=fields)
        await interaction.response.send_message(f"✅ поле «{self.label_input.value}» добавлено", ephemeral=True)


class EditFieldModal(Modal, title="Редактирование поля"):
    def __init__(self, field: dict):
        super().__init__()
        self.field_id = field["id"]
        self.label_input = TextInput(label="Заголовок поля", style=discord.TextStyle.short, max_length=45,
                                      default=field["label"], required=True)
        self.placeholder_input = TextInput(label="Пример (placeholder)", style=discord.TextStyle.short, max_length=100,
                                            default=field.get("placeholder", ""), required=False)
        self.max_length_input = TextInput(label="Лимит символов (число)", style=discord.TextStyle.short,
                                           default=str(field.get("max_length", 100)), required=False)
        self.add_item(self.label_input)
        self.add_item(self.placeholder_input)
        self.add_item(self.max_length_input)

    async def on_submit(self, interaction: discord.Interaction):
        settings = get_ticket_settings()
        fields = settings.get("fields", [])
        try:
            max_length = max(1, min(int(self.max_length_input.value), 1024))
        except ValueError:
            max_length = 100

        for f in fields:
            if f["id"] == self.field_id:
                f["label"] = self.label_input.value
                f["placeholder"] = self.placeholder_input.value or ""
                f["max_length"] = max_length
        update_ticket_settings(fields=fields)
        await interaction.response.send_message("✅ поле обновлено", ephemeral=True)


class FieldActionView(View):
    def __init__(self, field: dict):
        super().__init__(timeout=180)
        self.field = field

    @discord.ui.button(label="✏️ Текст", style=discord.ButtonStyle.blurple)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditFieldModal(self.field))

    @discord.ui.button(label="🔁 Сменить тип (одна строка / абзац)", style=discord.ButtonStyle.secondary)
    async def toggle_style(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = get_ticket_settings()
        fields = settings.get("fields", [])
        for f in fields:
            if f["id"] == self.field["id"]:
                f["style"] = "short" if f.get("style") == "paragraph" else "paragraph"
                new_style = f["style"]
        update_ticket_settings(fields=fields)
        await interaction.response.send_message(f"✅ тип поля теперь: {new_style}", ephemeral=True)

    @discord.ui.button(label="❗ Обязательное/необязательное", style=discord.ButtonStyle.secondary)
    async def toggle_required(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = get_ticket_settings()
        fields = settings.get("fields", [])
        for f in fields:
            if f["id"] == self.field["id"]:
                f["required"] = not f.get("required", True)
                new_req = f["required"]
        update_ticket_settings(fields=fields)
        await interaction.response.send_message(f"✅ теперь поле {'обязательное' if new_req else 'необязательное'}", ephemeral=True)

    @discord.ui.button(label="🗑 Удалить поле", style=discord.ButtonStyle.red)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = get_ticket_settings()
        fields = [f for f in settings.get("fields", []) if f["id"] != self.field["id"]]
        update_ticket_settings(fields=fields)
        await interaction.response.send_message(f"🗑 поле «{self.field['label']}» удалено", ephemeral=True)


class FieldPickSelect(discord.ui.Select):
    def __init__(self, fields: list):
        options = [
            discord.SelectOption(label=f["label"][:100], value=f["id"],
                                  description=("многострочное" if f.get("style") == "paragraph" else "одна строка"))
            for f in fields
        ] or [discord.SelectOption(label="нет полей", value="none")]
        super().__init__(placeholder="Выбери поле для редактирования/удаления...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("полей пока нет, сначала добавь", ephemeral=True)
            return
        settings = get_ticket_settings()
        field = next((f for f in settings.get("fields", []) if f["id"] == self.values[0]), None)
        if not field:
            await interaction.response.send_message("поле не найдено (возможно, уже удалено)", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(title=field["label"], description=_field_summary(field), color=0x2b2d31),
            view=FieldActionView(field),
            ephemeral=True,
        )


class FieldsMenuView(View):
    def __init__(self, settings: dict):
        super().__init__(timeout=180)
        self.add_item(FieldPickSelect(settings.get("fields", [])))
        self.fields_count = len(settings.get("fields", []))

    @discord.ui.button(label="➕ Короткое поле", style=discord.ButtonStyle.green, row=1)
    async def add_short(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.fields_count >= 5:
            await interaction.response.send_message("⚠️ уже максимум 5 полей", ephemeral=True)
            return
        await interaction.response.send_modal(AddFieldModal(style="short"))

    @discord.ui.button(label="➕ Многострочное поле", style=discord.ButtonStyle.green, row=1)
    async def add_paragraph(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.fields_count >= 5:
            await interaction.response.send_message("⚠️ уже максимум 5 полей", ephemeral=True)
            return
        await interaction.response.send_modal(AddFieldModal(style="paragraph"))


# ═══════════════════════════════════════════════
# Роли: пинг и доступ
# ═══════════════════════════════════════════════

class RoleSettingSelect(discord.ui.RoleSelect):
    def __init__(self, setting_key: str, current_ids: list, placeholder: str):
        super().__init__(
            placeholder=placeholder,
            min_values=0,
            max_values=25,
            default_values=[discord.Object(id=r, type=discord.Role) for r in current_ids],
        )
        self.setting_key = setting_key

    async def callback(self, interaction: discord.Interaction):
        role_ids = [r.id for r in self.values]
        update_ticket_settings(**{self.setting_key: role_ids})
        await interaction.response.send_message(
            f"✅ сохранено: {ro(role_ids)}" if role_ids else "✅ сохранено: список ролей очищен",
            ephemeral=True,
        )


class PingRolesView(View):
    def __init__(self, settings: dict):
        super().__init__(timeout=180)
        self.add_item(RoleSettingSelect("ping_role_ids", settings.get("ping_role_ids", []),
                                         "Роли, которые пингуются в новой заявке"))


class AccessRolesView(View):
    def __init__(self, settings: dict):
        super().__init__(timeout=180)
        self.add_item(RoleSettingSelect("access_role_ids", settings.get("access_role_ids", []),
                                         "Роли с доступом к кнопкам заявки (принять/отклонить/обзвон)"))


# ═══════════════════════════════════════════════
# Каналы тикет-системы
# ═══════════════════════════════════════════════

_CHANNEL_TYPE_CLASS = {
    discord.ChannelType.text: discord.TextChannel,
    discord.ChannelType.category: discord.CategoryChannel,
    discord.ChannelType.voice: discord.VoiceChannel,
}


class ChannelSettingSelect(discord.ui.ChannelSelect):
    def __init__(self, setting_key: str, current_id: int, placeholder: str, channel_types, row: int):
        default_cls = _CHANNEL_TYPE_CLASS.get(channel_types[0], discord.TextChannel)
        super().__init__(
            placeholder=placeholder,
            channel_types=channel_types,
            default_values=[discord.Object(id=current_id, type=default_cls)] if current_id else [],
            row=row,
        )
        self.setting_key = setting_key

    async def callback(self, interaction: discord.Interaction):
        update_ticket_settings(**{self.setting_key: self.values[0].id})
        await interaction.response.send_message(f"✅ сохранено: {self.values[0].mention}", ephemeral=True)


class ChannelsMenuView(View):
    def __init__(self, settings: dict):
        super().__init__(timeout=180)
        self.add_item(ChannelSettingSelect("channel_category", settings["channel_category"],
                                            "🗂️ Категория для каналов с тикетами", [discord.ChannelType.category], row=0))
        self.add_item(ChannelSettingSelect("channel_results", settings["channel_results"],
                                            "📨 Канал результатов", [discord.ChannelType.text], row=1))
        self.add_item(ChannelSettingSelect("channel_call", settings["channel_call"],
                                            "🎙️ Голосовой канал обзвона", [discord.ChannelType.voice], row=2))
        self.add_item(ChannelSettingSelect("channel_accepted_logs", settings["channel_accepted_logs"],
                                            "✅ Канал лога принятых заявок", [discord.ChannelType.text], row=3))
        self.add_item(ChannelSettingSelect("channel_rejected_logs", settings["channel_rejected_logs"],
                                            "❌ Канал лога отклонённых заявок", [discord.ChannelType.text], row=4))


# ═══════════════════════════════════════════════
# Статистика
# ═══════════════════════════════════════════════

def build_stats_embed() -> discord.Embed:
    try:
        open_tickets = get_tickets_collection().count_documents({})
    except Exception:
        open_tickets = "ошибка"

    settings = get_ticket_settings()
    embed = discord.Embed(title="📈 Статистика заявок", color=0x3498db)
    embed.add_field(name="Набор в семью", value="🟢 Открыт" if is_recruitment_open() else "🔴 Закрыт", inline=True)
    embed.add_field(name="Открытых заявок", value=str(open_tickets), inline=True)
    embed.add_field(name="Полей в форме", value=f"{len(settings.get('fields', []))}/5", inline=True)
    embed.add_field(name="Ролей для пинга", value=str(len(settings.get("ping_role_ids", []))), inline=True)
    embed.add_field(name="Ролей с доступом", value=str(len(settings.get("access_role_ids", []))), inline=True)
    embed.timestamp = discord.utils.utcnow()
    return embed


# ═══════════════════════════════════════════════
# Главная админ-панель
# ═══════════════════════════════════════════════

class AdminPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔁 Открыть/закрыть набор", style=discord.ButtonStyle.green, custom_id="admin_toggle_recruitment", row=0)
    async def toggle_recruitment(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        new_state = not is_recruitment_open()
        set_recruitment_open(new_state)
        await interaction.response.edit_message(embed=build_admin_panel_embed(interaction.guild), view=AdminPanelView())
        await resend_ticket_panel(interaction.guild, interaction.client.user)
        await interaction.followup.send(
            "✅ набор в семью открыт" if new_state else "✅ набор в семью закрыт", ephemeral=True
        )

    @discord.ui.button(label="✏️ Панель заявок", style=discord.ButtonStyle.secondary, custom_id="admin_edit_panel", row=0)
    async def edit_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        settings = get_ticket_settings()
        await interaction.response.send_message("Настройки панели заявок:", view=PanelMenuView(settings), ephemeral=True)

    @discord.ui.button(label="📈 Статистика", style=discord.ButtonStyle.secondary, custom_id="admin_stats", row=0)
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_stats_embed(), ephemeral=True)

    @discord.ui.button(label="📝 Поля заявки", style=discord.ButtonStyle.secondary, custom_id="admin_fields", row=1)
    async def fields(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        settings = get_ticket_settings()
        await interaction.response.send_message(embed=build_fields_embed(settings), view=FieldsMenuView(settings), ephemeral=True)

    @discord.ui.button(label="📣 Роли для пинга", style=discord.ButtonStyle.secondary, custom_id="admin_ping_roles", row=1)
    async def ping_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        settings = get_ticket_settings()
        await interaction.response.send_message("Какие роли пинговать в новой заявке:", view=PingRolesView(settings), ephemeral=True)

    @discord.ui.button(label="🔑 Роли с доступом", style=discord.ButtonStyle.secondary, custom_id="admin_access_roles", row=1)
    async def access_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        settings = get_ticket_settings()
        await interaction.response.send_message("Какие роли могут управлять заявками:", view=AccessRolesView(settings), ephemeral=True)

    @discord.ui.button(label="📂 Каналы", style=discord.ButtonStyle.secondary, custom_id="admin_channels", row=2)
    async def channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        settings = get_ticket_settings()
        await interaction.response.send_message("Каналы тикет-системы:", view=ChannelsMenuView(settings), ephemeral=True)


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

        for guild in self.bot.guilds:
            channel = guild.get_channel(ADMIN_PANEL_CHANNEL_ID)
            if channel is None:
                continue

            async for message in channel.history(limit=100):
                if message.author == self.bot.user:
                    await message.delete()

            await channel.send(embed=build_admin_panel_embed(guild), view=AdminPanelView())
            self._panel_posted_this_run = True
            return

        print(f"[ADMIN PANEL] канал {ADMIN_PANEL_CHANNEL_ID} не найден ни на одном сервере")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminPanelCog(bot))
