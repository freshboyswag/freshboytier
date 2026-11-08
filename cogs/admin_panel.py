import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View

from config import ADMIN_PANEL_CHANNEL_ID
from database import (
    get_ticket_settings, update_ticket_settings,
    is_recruitment_open, set_recruitment_open,
)
from cogs.tickets import has_ticket_access, TicketPanelView, build_ticket_panel_embed


# ═══════════════════════════════════════════════
# Пересылка панели заявок в её канал
# ═══════════════════════════════════════════════

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
    if image_url:
        content_embed.set_image(url=image_url)

    msg = await channel.send(embed=content_embed, view=TicketPanelView())
    update_ticket_settings(ticket_panel_message_id=msg.id)
    return True


# ═══════════════════════════════════════════════
# Главный эмбед админ-панели
# ═══════════════════════════════════════════════

def build_admin_panel_embed() -> discord.Embed:
    open_now = is_recruitment_open()
    return discord.Embed(
        title="⚙️ Админ-панель заявок",
        description=f"## Набор в семью: {'🟢 Открыт' if open_now else '🔴 Закрыт'}",
        color=0x57F287 if open_now else 0xED4245,
    )


# ═══════════════════════════════════════════════
# Панель заявок: текст / картинка
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
            label="Ссылка на фотку",
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
        await interaction.followup.send(
            "✅ текст сохранён. Нажми «Переотправить», чтобы обновить панель в канале.", ephemeral=True
        )


class PanelMenuView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✏️ Текст и фотка", style=discord.ButtonStyle.blurple, row=0)
    async def edit_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = get_ticket_settings()
        await interaction.response.send_modal(EditPanelModal(settings))

    @discord.ui.button(label="📝 Поля формы", style=discord.ButtonStyle.blurple, row=0)
    async def fields(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = get_ticket_settings()
        await interaction.response.send_message(
            embed=build_fields_embed(settings), view=FieldsMenuView(settings), ephemeral=True
        )

    @discord.ui.button(label="📤 Переотправить", style=discord.ButtonStyle.secondary, row=0)
    async def resend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        ok = await resend_ticket_panel(interaction.guild, interaction.client.user)
        await interaction.followup.send("✅ переслано" if ok else "⚠️ канал панели заявок не найден", ephemeral=True)


# ═══════════════════════════════════════════════
# Поля заявки — 5 фиксированных слотов.
# Если у поля пустой заголовок — оно считается выключенным
# и не показывается в форме, но остаётся видно в админке.
# ═══════════════════════════════════════════════

def _field_line(field: dict) -> str:
    if not field.get("label"):
        return "— поле отключено (нет текста) —"
    kind = "многострочное" if field.get("style") == "paragraph" else "однострочное"
    req = "обязательное" if field.get("required", True) else "необязательное"
    return f"пример: `{field.get('placeholder') or '—'}` · {kind}, {req}, лимит {field.get('max_length', 100)} символов"


def build_fields_embed(settings: dict) -> discord.Embed:
    embed = discord.Embed(title="Поля заявки", color=0x2b2d31)
    for i, field in enumerate(settings.get("fields", []), start=1):
        name = f"{i}. {field['label']}" if field.get("label") else f"{i}. (отключено)"
        embed.add_field(name=name, value=_field_line(field), inline=False)
    return embed


def build_field_detail_embed(field: dict) -> discord.Embed:
    if not field.get("label"):
        return discord.Embed(
            title="(поле отключено)",
            description="Заголовок пуст — поле не показывается в форме заявки.\nНажми «Текст», чтобы вписать заголовок и включить его обратно.",
            color=0x2b2d31,
        )
    embed = discord.Embed(title=field["label"], description=_field_line(field), color=0x2b2d31)
    return embed


class EditFieldModal(Modal, title="Текст поля"):
    def __init__(self, field: dict, origin_interaction: discord.Interaction):
        super().__init__()
        self.field_id = field["id"]
        self.origin_interaction = origin_interaction
        self.label_input = TextInput(
            label="Заголовок (пусто — выключить поле)",
            style=discord.TextStyle.short,
            default=field.get("label", ""),
            max_length=45,
            required=False,
        )
        self.placeholder_input = TextInput(
            label="Пример (placeholder)",
            style=discord.TextStyle.short,
            default=field.get("placeholder", ""),
            max_length=100,
            required=False,
        )
        self.max_length_input = TextInput(
            label="Лимит символов (число)",
            style=discord.TextStyle.short,
            default=str(field.get("max_length", 100)),
            required=False,
        )
        self.add_item(self.label_input)
        self.add_item(self.placeholder_input)
        self.add_item(self.max_length_input)

    async def on_submit(self, interaction: discord.Interaction):
        settings = get_ticket_settings()
        fields = settings.get("fields", [])
        updated = None
        try:
            max_length = max(1, min(int(self.max_length_input.value), 1024)) if self.max_length_input.value.strip() else 100
        except ValueError:
            max_length = 100

        for f in fields:
            if f["id"] == self.field_id:
                f["label"] = self.label_input.value.strip()
                f["placeholder"] = self.placeholder_input.value.strip()
                f["max_length"] = max_length
                updated = f
        update_ticket_settings(fields=fields)

        if updated:
            try:
                await self.origin_interaction.edit_original_response(
                    embed=build_field_detail_embed(updated), view=FieldActionView(updated)
                )
            except discord.HTTPException:
                pass
        await interaction.response.defer()


class FieldActionView(View):
    def __init__(self, field: dict):
        super().__init__(timeout=None)
        self.field = field

        edit_btn = discord.ui.Button(label="✏️ Текст", style=discord.ButtonStyle.blurple, row=0)
        edit_btn.callback = self.edit_text
        self.add_item(edit_btn)

        style_btn = discord.ui.Button(label="🔁 Одна строка / абзац", style=discord.ButtonStyle.blurple, row=0)
        style_btn.callback = self.toggle_style
        self.add_item(style_btn)

        required_btn = discord.ui.Button(
            label="❗ Обязательное/необязательное",
            style=discord.ButtonStyle.red if field.get("required", True) else discord.ButtonStyle.blurple,
            row=0,
        )
        required_btn.callback = self.toggle_required
        self.add_item(required_btn)

    def _save_and_get(self, mutate) -> dict:
        settings = get_ticket_settings()
        fields = settings.get("fields", [])
        updated = self.field
        for f in fields:
            if f["id"] == self.field["id"]:
                mutate(f)
                updated = f
        update_ticket_settings(fields=fields)
        return updated

    async def edit_text(self, interaction: discord.Interaction):
        await interaction.response.send_modal(EditFieldModal(self.field, origin_interaction=interaction))

    async def toggle_style(self, interaction: discord.Interaction):
        updated = self._save_and_get(
            lambda f: f.__setitem__("style", "short" if f.get("style") == "paragraph" else "paragraph")
        )
        await interaction.response.edit_message(embed=build_field_detail_embed(updated), view=FieldActionView(updated))

    async def toggle_required(self, interaction: discord.Interaction):
        updated = self._save_and_get(
            lambda f: f.__setitem__("required", not f.get("required", True))
        )
        await interaction.response.edit_message(embed=build_field_detail_embed(updated), view=FieldActionView(updated))


class FieldPickSelect(discord.ui.Select):
    def __init__(self, fields: list):
        options = [
            discord.SelectOption(
                label=(f"{i}. {f['label']}" if f.get("label") else f"{i}. (отключено)")[:100],
                value=f["id"],
            )
            for i, f in enumerate(fields, start=1)
        ]
        super().__init__(placeholder="Выбери поле для редактирования...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        settings = get_ticket_settings()
        field = next((f for f in settings.get("fields", []) if f["id"] == self.values[0]), None)
        if not field:
            await interaction.response.send_message("поле не найдено", ephemeral=True)
            return
        await interaction.response.edit_message(embed=build_field_detail_embed(field), view=FieldActionView(field))


class FieldsMenuView(View):
    def __init__(self, settings: dict):
        super().__init__(timeout=None)
        self.add_item(FieldPickSelect(settings.get("fields", [])))


# ═══════════════════════════════════════════════
# Каналы тикет-системы — одна форма, все айди уже вбиты
# ═══════════════════════════════════════════════

class ChannelsModal(Modal, title="Каналы тикет-системы"):
    def __init__(self, settings: dict):
        super().__init__()
        self.category_input = TextInput(
            label="Категория тикетов (ID)", style=discord.TextStyle.short,
            default=str(settings["channel_category"]), max_length=25,
        )
        self.results_input = TextInput(
            label="Канал результатов (ID)", style=discord.TextStyle.short,
            default=str(settings["channel_results"]), max_length=25,
        )
        self.call_input = TextInput(
            label="Голосовой канал обзвона (ID)", style=discord.TextStyle.short,
            default=str(settings["channel_call"]), max_length=25,
        )
        self.accepted_input = TextInput(
            label="Лог принятых заявок (ID)", style=discord.TextStyle.short,
            default=str(settings["channel_accepted_logs"]), max_length=25,
        )
        self.rejected_input = TextInput(
            label="Лог отклонённых заявок (ID)", style=discord.TextStyle.short,
            default=str(settings["channel_rejected_logs"]), max_length=25,
        )
        for item in (self.category_input, self.results_input, self.call_input, self.accepted_input, self.rejected_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            update_ticket_settings(
                channel_category=int(self.category_input.value.strip()),
                channel_results=int(self.results_input.value.strip()),
                channel_call=int(self.call_input.value.strip()),
                channel_accepted_logs=int(self.accepted_input.value.strip()),
                channel_rejected_logs=int(self.rejected_input.value.strip()),
            )
        except ValueError:
            await interaction.response.send_message("⚠️ все поля должны быть числовыми ID каналов", ephemeral=True)
            return
        await interaction.response.send_message("✅ каналы сохранены", ephemeral=True)


# ═══════════════════════════════════════════════
# Роли тикет-системы — одна форма
# ═══════════════════════════════════════════════

def _parse_role_ids(raw: str) -> list:
    ids = []
    for token in raw.replace(",", " ").split():
        token = token.strip("<@&> ")
        if token.isdigit():
            ids.append(int(token))
    return ids


class RolesModal(Modal, title="Роли тикет-системы"):
    def __init__(self, settings: dict):
        super().__init__()
        self.access_input = TextInput(
            label="Роли с доступом к тикетам (id через пробел)",
            style=discord.TextStyle.paragraph,
            default=" ".join(str(r) for r in settings.get("access_role_ids", [])),
            required=False,
            max_length=600,
        )
        self.ping_input = TextInput(
            label="Роли которые будут тегнуты в новом тикете",
            style=discord.TextStyle.paragraph,
            default=" ".join(str(r) for r in settings.get("ping_role_ids", [])),
            required=False,
            max_length=600,
        )
        self.add_item(self.access_input)
        self.add_item(self.ping_input)

    async def on_submit(self, interaction: discord.Interaction):
        access_ids = _parse_role_ids(self.access_input.value)
        ping_ids = _parse_role_ids(self.ping_input.value)
        update_ticket_settings(access_role_ids=access_ids, ping_role_ids=ping_ids)
        await interaction.response.send_message(
            f"✅ сохранено · доступ: {len(access_ids)} ролей, пинг: {len(ping_ids)} ролей", ephemeral=True
        )


# ═══════════════════════════════════════════════
# Главная админ-панель
# ═══════════════════════════════════════════════

class AdminPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)
        open_now = is_recruitment_open()

        toggle_btn = discord.ui.Button(
            label="🟢 Набор открыт" if open_now else "🔴 Набор закрыт",
            style=discord.ButtonStyle.green if open_now else discord.ButtonStyle.red,
            custom_id="admin_toggle_recruitment",
            row=0,
        )
        toggle_btn.callback = self.toggle_recruitment
        self.add_item(toggle_btn)

        panel_btn = discord.ui.Button(label="📋 Панель", style=discord.ButtonStyle.secondary,
                                       custom_id="admin_open_panel", row=0)
        panel_btn.callback = self.open_panel_menu
        self.add_item(panel_btn)

        channels_btn = discord.ui.Button(label="📂 Каналы", style=discord.ButtonStyle.secondary,
                                          custom_id="admin_channels", row=0)
        channels_btn.callback = self.open_channels
        self.add_item(channels_btn)

        roles_btn = discord.ui.Button(label="🔑 Роли", style=discord.ButtonStyle.secondary,
                                       custom_id="admin_roles", row=0)
        roles_btn.callback = self.open_roles
        self.add_item(roles_btn)

    async def toggle_recruitment(self, interaction: discord.Interaction):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        new_state = not is_recruitment_open()
        set_recruitment_open(new_state)
        await interaction.response.edit_message(embed=build_admin_panel_embed(), view=AdminPanelView())

        settings = get_ticket_settings()
        channel = interaction.guild.get_channel(settings["channel_tickets"])
        msg_id = settings.get("ticket_panel_message_id")
        if channel and msg_id:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(view=TicketPanelView())
            except (discord.NotFound, discord.HTTPException):
                pass

        await interaction.followup.send("✅ набор открыт" if new_state else "✅ набор закрыт", ephemeral=True)

    async def open_panel_menu(self, interaction: discord.Interaction):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.send_message("Управление панелью заявок:", view=PanelMenuView(), ephemeral=True)

    async def open_channels(self, interaction: discord.Interaction):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        settings = get_ticket_settings()
        await interaction.response.send_modal(ChannelsModal(settings))

    async def open_roles(self, interaction: discord.Interaction):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        settings = get_ticket_settings()
        await interaction.response.send_modal(RolesModal(settings))


# ═══════════════════════════════════════════════
# Ког
# ═══════════════════════════════════════════════

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

            await channel.send(embed=build_admin_panel_embed(), view=AdminPanelView())
            # панель заявок при рестарте НЕ трогаем — только админку
            self._panel_posted_this_run = True
            return

        print(f"[ADMIN PANEL] канал {ADMIN_PANEL_CHANNEL_ID} не найден ни на одном сервере")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminPanelCog(bot))
