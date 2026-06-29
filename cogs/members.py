import io
import json
import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View
from datetime import timezone, timedelta
from pymongo import ReturnDocument

from config import (
    RESULTS_CHANNEL_ID, LOGS_ACCEPTED_ID
)
from database import get_db, get_tickets_collection

# ───────────────────────────────────────────────
# КОНСТАНТЫ
# ───────────────────────────────────────────────

DATABASE_CHANNEL_ID = 1519121489486544937

DB_VIEW_ROLES = {1510604555040198816, 1510601395999346819, 1510610391267545138, 1510610350532329642, 1510614323754434670}
DB_EDIT_ROLES = {1510604555040198816, 1510601395999346819}

MEMBER_ROLES = {
    "Tier S":     1510603296321306774,
    "Tier A":     1510603326331293846,
    "Tier B":     1510603348276023547,
    "Antisocial": 1510600886563504148,
    "Test":       1510603939664629891,
    "Qual":       1510604916207390740,
}

UTC3 = timezone(timedelta(hours=3))

# ───────────────────────────────────────────────
# ХЕЛПЕРЫ
# ───────────────────────────────────────────────

def has_db_view_role(member: discord.Member) -> bool:
    return any(r.id in DB_VIEW_ROLES for r in member.roles)

def has_db_edit_role(member: discord.Member) -> bool:
    return any(r.id in DB_EDIT_ROLES for r in member.roles)

def get_members_game_col():
    return get_db()["members_game"]

def get_members_server_col():
    return get_db()["members_server"]

def get_members_uid_counter() -> int:
    try:
        col = get_db()["counters"]
        doc = col.find_one_and_update(
            {"_id": "members_uid_counter"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return doc["value"]
    except Exception as e:
        print(f"[ERROR] members uid counter: {e}")
        return 0

def fmt_time(dt=None) -> str:
    if dt is None:
        dt = discord.utils.utcnow()
    local = dt.astimezone(UTC3)
    return local.strftime("%d.%m.%Y %H:%M")

def format_fullname(raw: str) -> str:
    """имя_фамилия -> Имя Фамилия"""
    if not raw or raw == "-":
        return "-"
    return raw.replace("_", " ").title()

def build_database_embed() -> discord.Embed:
    embed = discord.Embed(title="База данных участников", color=0x1ABC9C)
    embed.description = "Используй кнопки ниже для управления базой данных"
    embed.timestamp = discord.utils.utcnow()
    return embed


# ───────────────────────────────────────────────
# ЛОГИРОВАНИЕ В ТИКЕТЫ
# ───────────────────────────────────────────────

async def ticket_log_append(channel_id: str, entry: dict):
    try:
        col = get_tickets_collection()
        ticket = col.find_one({"channel_id": channel_id})
        if not ticket:
            return
        discord_id = ticket.get("applicant_id")
        if not discord_id:
            return
        get_members_server_col().update_one(
            {"discord_id": discord_id},
            {"$push": {"ticket_log": entry}},
            upsert=True
        )
    except Exception as e:
        print(f"[ERROR] ticket_log_append: {e}")


# ───────────────────────────────────────────────
# ПАГИНАЦИЯ
# ───────────────────────────────────────────────

class PerPageModal(Modal, title="Записей на странице"):
    amount = TextInput(
        label="Количество (1–200)",
        placeholder="например: 50",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=3
    )

    def __init__(self, list_view: "MembersListView"):
        super().__init__()
        self.list_view = list_view

    async def on_submit(self, interaction: discord.Interaction):
        val = self.amount.value.strip()
        if not val.isdigit():
            await interaction.response.send_message("введи число", ephemeral=True)
            return
        n = int(val)
        if n < 1 or n > 200:
            await interaction.response.send_message("число должно быть от 1 до 200", ephemeral=True)
            return
        self.list_view.per_page = n
        self.list_view.page = 0
        self.list_view._update_buttons()
        await interaction.response.edit_message(embed=self.list_view.get_page_embed(), view=self.list_view)


class MembersListView(View):
    def __init__(self, records: list, page: int = 0, per_page: int = 35):
        super().__init__(timeout=None)
        self.records = records
        self.page = page
        self.per_page = per_page
        self._update_buttons()

    def _update_buttons(self):
        total_pages = max(1, (len(self.records) + self.per_page - 1) // self.per_page)
        for item in self.children:
            if hasattr(item, "custom_id"):
                if item.custom_id == "ml_prev":
                    item.disabled = self.page == 0
                elif item.custom_id == "ml_next":
                    item.disabled = self.page >= total_pages - 1
                elif item.custom_id == "ml_per_page":
                    item.label = f"{self.per_page}/стр"

    def get_page_embed(self) -> discord.Embed:
        total = len(self.records)
        total_pages = max(1, (total + self.per_page - 1) // self.per_page)
        start = self.page * self.per_page
        end = min(start + self.per_page, total)
        page_records = self.records[start:end]

        lines = []
        for r in page_records:
            uid = r.get("uid", "?")
            username = r.get("discord_username", "-")
            dsid = r.get("discord_id", "-")
            full_name = format_fullname(r.get("full_name", "-"))
            static = r.get("static", "-")
            note = r.get("note", "-")

            mention = f"<@{dsid}>" if dsid and dsid != "-" else "-"
            line = f"`#{uid}` | {mention} (`{username}`) | {dsid} | {full_name} | {static}"
            if note and note != "-":
                line += f" | **{note}**"
            lines.append(line)

        description = "\n".join(lines) if lines else "пусто"
        # Discord ограничение — 4096 символов в description
        if len(description) > 4000:
            description = description[:4000] + "\n... (обрезано)"

        embed = discord.Embed(
            title="Учёт участников",
            description=description,
            color=0x1ABC9C
        )
        embed.set_footer(text=f"Страница {self.page + 1}/{total_pages} | {end - start} из {total}")
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.grey, custom_id="ml_prev")
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.grey, custom_id="ml_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        total_pages = max(1, (len(self.records) + self.per_page - 1) // self.per_page)
        self.page = min(total_pages - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)

    @discord.ui.button(label="35/стр", style=discord.ButtonStyle.blurple, custom_id="ml_per_page")
    async def change_per_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PerPageModal(list_view=self))


# ───────────────────────────────────────────────
# ПОИСК
# ───────────────────────────────────────────────

class MemberSearchModal(Modal, title="Поиск участника"):
    query = TextInput(
        label="uid, Discord ID, имя_фамилия или статик",
        placeholder="например: 42 или 186404 или harukaze_antisocial",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        q = self.query.value.strip()
        col = get_members_game_col()

        doc = None
        if q.isdigit() and len(q) < 10:
            doc = col.find_one({"uid": int(q)})
        if not doc and q.isdigit():
            doc = col.find_one({"discord_id": q})
        if not doc:
            doc = col.find_one({"full_name": q})
        if not doc:
            doc = col.find_one({"static": q})
        if not doc:
            doc = col.find_one({"discord_username": q})

        if not doc:
            await interaction.followup.send("участник не найден", ephemeral=True)
            return

        guild = interaction.guild
        discord_id = doc.get("discord_id")
        member = guild.get_member(int(discord_id)) if discord_id and discord_id != "-" else None

        embed = discord.Embed(title=f"Участник #{doc.get('uid', '?')}", color=0x1ABC9C)
        embed.add_field(
            name="Discord",
            value=f"<@{discord_id}> (@{doc.get('discord_username', '-')})" if discord_id and discord_id != "-" else "-",
            inline=False
        )
        embed.add_field(name="Discord ID", value=discord_id or "-", inline=True)
        embed.add_field(name="Имя Фамилия", value=format_fullname(doc.get("full_name", "-")), inline=True)
        embed.add_field(name="Статик", value=doc.get("static", "-"), inline=True)
        embed.add_field(name="Заметка", value=doc.get("note", "-"), inline=False)

        if member:
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(
                name="На сервере с",
                value=discord.utils.format_dt(member.joined_at, style="D") if member.joined_at else "-",
                inline=True
            )
            embed.add_field(name="Акк создан", value=discord.utils.format_dt(member.created_at, style="D"), inline=True)

        srv = get_members_server_col().find_one({"discord_id": discord_id})
        if srv:
            if srv.get("accepted_by"):
                embed.add_field(name="Принял", value=f"<@{srv['accepted_by']}>", inline=True)
            if srv.get("accepted_at"):
                embed.add_field(name="Дата принятия", value=srv["accepted_at"], inline=True)
            if srv.get("roles_given"):
                embed.add_field(name="Роли при принятии", value=", ".join(srv["roles_given"]), inline=False)

        view = MemberEditView(doc=doc, discord_id=discord_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# ───────────────────────────────────────────────
# РЕДАКТИРОВАНИЕ
# ───────────────────────────────────────────────

class MemberEditFieldModal(Modal):
    def __init__(self, doc: dict, field: str):
        labels = {
            "discord_username": "Discord Username",
            "discord_id": "Discord ID",
            "full_name": "Имя Фамилия (имя_фамилия)",
            "static": "Статик",
            "note": "Заметка",
        }
        super().__init__(title=f"Изменить: {labels.get(field, field)}")
        self.doc = doc
        self.field = field
        self.value_input = TextInput(
            label=labels.get(field, field),
            default=str(doc.get(field, "")),
            style=discord.TextStyle.short,
            min_length=1,
            max_length=200
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        col = get_members_game_col()
        col.update_one({"uid": self.doc["uid"]}, {"$set": {self.field: self.value_input.value.strip()}})
        await interaction.followup.send(f"✅ `{self.field}` обновлено", ephemeral=True)


class MemberEditAllModal(Modal, title="Изменить все данные"):
    discord_username = TextInput(label="Discord Username", style=discord.TextStyle.short, max_length=100)
    discord_id_field = TextInput(label="Discord ID", style=discord.TextStyle.short, max_length=20)
    full_name = TextInput(label="Имя Фамилия (имя_фамилия)", style=discord.TextStyle.short, max_length=100)
    static = TextInput(label="Статик", style=discord.TextStyle.short, max_length=20)
    note = TextInput(label="Заметка", style=discord.TextStyle.short, max_length=300, required=False)

    def __init__(self, doc: dict):
        super().__init__()
        self.doc = doc

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        col = get_members_game_col()
        col.update_one({"uid": self.doc["uid"]}, {"$set": {
            "discord_username": self.discord_username.value.strip(),
            "discord_id": self.discord_id_field.value.strip(),
            "full_name": self.full_name.value.strip(),
            "static": self.static.value.strip(),
            "note": self.note.value.strip() if self.note.value else "-",
        }})
        await interaction.followup.send("✅ Все данные обновлены", ephemeral=True)


class MemberDeleteConfirmView(View):
    def __init__(self, doc: dict, discord_id: str):
        super().__init__(timeout=30)
        self.doc = doc
        self.discord_id = discord_id

    @discord.ui.button(label="✅ Да, удалить", style=discord.ButtonStyle.red, custom_id="member_delete_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = self.doc.get("uid", "?")
        try:
            get_members_game_col().delete_one({"uid": self.doc["uid"]})
            get_members_server_col().delete_one({"discord_id": self.discord_id})
        except Exception as e:
            print(f"[ERROR] delete member: {e}")
            await interaction.response.edit_message(content="❌ ошибка при удалении", view=None)
            return
        await interaction.response.edit_message(content=f"✅ Запись uid #{uid} удалена", view=None)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.grey, custom_id="member_delete_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="отменено", view=None)


class MemberEditView(View):
    def __init__(self, doc: dict, discord_id: str):
        super().__init__(timeout=120)
        self.doc = doc
        self.discord_id = discord_id

    @discord.ui.button(label="✏️ Изменить данные", style=discord.ButtonStyle.blurple, custom_id="member_edit")
    async def edit_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_edit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        options = [
            discord.SelectOption(label="Discord Username", value="discord_username"),
            discord.SelectOption(label="Discord ID", value="discord_id"),
            discord.SelectOption(label="Имя Фамилия", value="full_name"),
            discord.SelectOption(label="Статик", value="static"),
            discord.SelectOption(label="Заметка", value="note"),
            discord.SelectOption(label="Все данные сразу", value="all"),
        ]
        select = discord.ui.Select(placeholder="Что изменить?", options=options)

        async def select_callback(select_interaction: discord.Interaction):
            field = select.values[0]
            if field == "all":
                await select_interaction.response.send_modal(MemberEditAllModal(doc=self.doc))
            else:
                await select_interaction.response.send_modal(MemberEditFieldModal(doc=self.doc, field=field))

        select.callback = select_callback
        view = View(timeout=60)
        view.add_item(select)
        await interaction.response.send_message("Выбери что изменить:", view=view, ephemeral=True)

    @discord.ui.button(label="🗑️ Удалить запись", style=discord.ButtonStyle.red, custom_id="member_delete")
    async def delete_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_edit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        uid = self.doc.get("uid", "?")
        username = self.doc.get("discord_username", "?")
        confirm_view = MemberDeleteConfirmView(doc=self.doc, discord_id=self.discord_id)
        await interaction.response.send_message(
            f"Удалить запись **uid #{uid}** (@{username})? Это действие необратимо.",
            view=confirm_view,
            ephemeral=True
        )


# ───────────────────────────────────────────────
# СОЗДАНИЕ ЗАПИСИ ВРУЧНУЮ
# ───────────────────────────────────────────────

class MemberCreateModal(Modal, title="Создать запись участника"):
    discord_username = TextInput(label="Discord Username", style=discord.TextStyle.short, max_length=100)
    discord_id_field = TextInput(label="Discord ID", style=discord.TextStyle.short, max_length=20)
    full_name = TextInput(label="Имя Фамилия (имя_фамилия)", placeholder="имя_фамилия или -", style=discord.TextStyle.short, max_length=100)
    static = TextInput(label="Статик", placeholder="номер или -", style=discord.TextStyle.short, max_length=20)
    note = TextInput(label="Заметка", style=discord.TextStyle.short, max_length=300, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        discord_id = self.discord_id_field.value.strip()
        col = get_members_game_col()

        existing = col.find_one({"discord_id": discord_id})
        if existing:
            await interaction.followup.send(
                f"запись с Discord ID `{discord_id}` уже существует (uid #{existing['uid']})", ephemeral=True
            )
            return

        uid = get_members_uid_counter()
        guild = interaction.guild
        member = guild.get_member(int(discord_id)) if discord_id.isdigit() else None

        doc = {
            "uid": uid,
            "discord_username": self.discord_username.value.strip(),
            "discord_id": discord_id,
            "full_name": self.full_name.value.strip() or "-",
            "static": self.static.value.strip() or "-",
            "note": self.note.value.strip() if self.note.value else "-",
        }
        col.insert_one(doc)

        srv_doc = {
            "discord_id": discord_id,
            "added_manually": True,
            "added_by": str(interaction.user.id),
            "added_at": fmt_time(),
            "ticket_log": [],
            "roles_given": [],
        }
        if member:
            srv_doc["joined_server_at"] = member.joined_at.isoformat() if member.joined_at else None
            srv_doc["account_created_at"] = member.created_at.isoformat()
        get_members_server_col().insert_one(srv_doc)

        await interaction.followup.send(f"✅ Запись создана: uid #{uid}", ephemeral=True)


# ───────────────────────────────────────────────
# ПРИНЯТИЕ С МОДАЛКОЙ И ВЫБОРОМ РОЛЕЙ
# (вызывается из cogs/tickets.py)
# ───────────────────────────────────────────────

class AcceptGameDataModal(Modal, title="Игровые данные участника"):
    full_name = TextInput(
        label="Имя Фамилия (имя_фамилия)",
        placeholder="harukaze_antisocial",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=100
    )
    static = TextInput(
        label="Статик",
        placeholder="186404",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=20
    )
    note = TextInput(
        label="Заметка",
        placeholder="необязательно",
        style=discord.TextStyle.short,
        required=False,
        max_length=300
    )

    def __init__(self, ticket_data: dict, applicant: discord.Member, ticket_channel):
        super().__init__()
        self.ticket_data = ticket_data
        self.applicant = applicant
        self.ticket_channel = ticket_channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        game_data = {
            "full_name": self.full_name.value.strip(),
            "static": self.static.value.strip(),
            "note": self.note.value.strip() if self.note.value else "-",
        }

        ticket_data = self.ticket_data
        applicant = self.applicant
        ticket_channel = self.ticket_channel

        # Select для выбора ролей
        options = [
            discord.SelectOption(label=name, value=str(role_id))
            for name, role_id in MEMBER_ROLES.items()
        ]
        select = discord.ui.Select(
            placeholder="Выбери роли для выдачи...",
            options=options,
            min_values=1,
            max_values=len(options)
        )

        async def select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer(ephemeral=True)

            guild = select_interaction.guild
            selected_role_ids = [int(v) for v in select.values]

            # Выдаём роли
            roles_given = []
            for role_id in selected_role_ids:
                role = guild.get_role(role_id)
                if role and applicant:
                    try:
                        await applicant.add_roles(role, reason="принятие в семью")
                        roles_given.append(role.name)
                    except Exception as e:
                        print(f"[ERROR] выдача роли {role_id}: {e}")

            log_time = fmt_time()
            log_entry = {
                "type": "log",
                "time": log_time,
                "text": (
                    f"[logs][{log_time}] {select_interaction.user.display_name} выдал роли: "
                    f"{', '.join(roles_given)} и заполнил игровые данные "
                    f"(имя: {game_data['full_name']}, статик: {game_data['static']}, заметка: {game_data['note']})"
                )
            }
            await ticket_log_append(str(ticket_channel.id), log_entry)

            # Сохраняем в members_game
            discord_id = str(applicant.id) if applicant else ticket_data.get("applicant_id")
            col_game = get_members_game_col()
            existing = col_game.find_one({"discord_id": discord_id})
            if existing:
                col_game.update_one({"discord_id": discord_id}, {"$set": {
                    "full_name": game_data["full_name"],
                    "static": game_data["static"],
                    "discord_username": applicant.name if applicant else existing.get("discord_username", "-"),
                }})
            else:
                uid = get_members_uid_counter()
                col_game.insert_one({
                    "uid": uid,
                    "discord_username": applicant.name if applicant else "-",
                    "discord_id": discord_id,
                    "full_name": game_data["full_name"],
                    "static": game_data["static"],
                    "note": game_data["note"],
                })

            # Сохраняем в members_server
            get_members_server_col().update_one(
                {"discord_id": discord_id},
                {"$set": {
                    "accepted_by": str(select_interaction.user.id),
                    "accepted_at": log_time,
                    "roles_given": roles_given,
                    "ticket_fields": ticket_data.get("fields", {}),
                    "joined_server_at": applicant.joined_at.isoformat() if applicant and applicant.joined_at else None,
                    "account_created_at": applicant.created_at.isoformat() if applicant else None,
                }},
                upsert=True
            )

            accept_log = {
                "type": "log",
                "time": fmt_time(),
                "text": f"[logs][{fmt_time()}] {select_interaction.user.display_name} принял заявку и завершил рассмотрение"
            }
            await ticket_log_append(str(ticket_channel.id), accept_log)

            # Закрываем тикет
            guild2 = select_interaction.guild
            results_channel = guild2.get_channel(RESULTS_CHANNEL_ID)
            logs_channel = guild2.get_channel(LOGS_ACCEPTED_ID)

            results_msg_id = ticket_data.get("results_msg_id")
            if results_msg_id and results_channel:
                try:
                    msg = await results_channel.fetch_message(int(results_msg_id))
                    await msg.delete()
                except Exception:
                    pass

            embed = discord.Embed(color=0x2ecc71)
            embed.description = (
                f"Заявка пользователя {applicant.mention if applicant else ticket_data['applicant_name']}\n\n"
                f"На вступление в семью была принята! 🎉\n"
                f"Рассматривал заявку: {select_interaction.user.mention}"
            )
            if results_channel:
                await results_channel.send(embed=embed)

            if logs_channel:
                log_embed = discord.Embed(title="Заявка принята", color=0x2ecc71)
                log_embed.add_field(
                    name="Пользователь",
                    value=f"{applicant.mention if applicant else ticket_data['applicant_name']} (`{ticket_data['applicant_name']}`)",
                    inline=False
                )
                fields = ticket_data.get("fields", {})
                log_embed.add_field(name="ник / статик / имя / возраст", value=fields.get("nick", "-"), inline=False)
                log_embed.add_field(name="средний онлайн / прайм-тайм", value=fields.get("online", "-"), inline=False)
                log_embed.add_field(name="история семей", value=fields.get("history", "-"), inline=False)
                log_embed.add_field(name="откуда узнали о семье", value=fields.get("source", "-"), inline=False)
                log_embed.add_field(name="откаты", value=fields.get("clips", "-"), inline=False)
                log_embed.add_field(name="Выданные роли", value=", ".join(roles_given) if roles_given else "-", inline=False)
                log_embed.add_field(name="Принял", value=select_interaction.user.mention, inline=False)
                log_embed.timestamp = discord.utils.utcnow()
                await logs_channel.send(embed=log_embed)

            try:
                from database import get_tickets_collection as _gtc
                _gtc().delete_one({"channel_id": str(ticket_channel.id)})
            except Exception as e:
                print(f"[ERROR] MongoDB ticket delete: {e}")

            await ticket_channel.delete()
            await select_interaction.followup.send("✅ готово", ephemeral=True)

        select.callback = select_callback
        view = View(timeout=120)
        view.add_item(select)
        await interaction.followup.send("Выбери роли для выдачи:", view=view, ephemeral=True)


# ───────────────────────────────────────────────
# ГЛАВНАЯ ПАНЕЛЬ БД
# ───────────────────────────────────────────────

class DatabasePanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Учёт участников", style=discord.ButtonStyle.blurple, custom_id="db_list")
    async def db_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_view_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        records = list(get_members_game_col().find({}, {"_id": 0}).sort("uid", 1))
        view = MembersListView(records=records)
        await interaction.followup.send(embed=view.get_page_embed(), view=view, ephemeral=True)

    @discord.ui.button(label="🔍 Инфо по участнику", style=discord.ButtonStyle.grey, custom_id="db_search")
    async def db_search(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_view_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.send_modal(MemberSearchModal())

    @discord.ui.button(label="➕ Создать запись", style=discord.ButtonStyle.green, custom_id="db_create")
    async def db_create(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_edit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.send_modal(MemberCreateModal())

    @discord.ui.button(label="🔄 Синхронизировать", style=discord.ButtonStyle.grey, custom_id="db_sync")
    async def db_sync(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_edit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        col_game = get_members_game_col()
        col_srv = get_members_server_col()
        records = list(col_game.find({}))
        updated = 0

        for rec in records:
            discord_id = rec.get("discord_id")
            if not discord_id or discord_id == "-":
                continue
            try:
                member = guild.get_member(int(discord_id))
                if not member:
                    member = await guild.fetch_member(int(discord_id))
                if member:
                    col_game.update_one({"_id": rec["_id"]}, {"$set": {
                        "discord_username": member.name,
                    }})
                    col_srv.update_one({"discord_id": discord_id}, {"$set": {
                        "joined_server_at": member.joined_at.isoformat() if member.joined_at else None,
                        "account_created_at": member.created_at.isoformat(),
                    }}, upsert=True)
                    updated += 1
            except Exception:
                pass

        await interaction.followup.send(f"✅ Синхронизировано: {updated} из {len(records)}", ephemeral=True)

    @discord.ui.button(label="📥 Скачать БД", style=discord.ButtonStyle.grey, custom_id="db_download")
    async def db_download(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_view_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        game_records = list(get_members_game_col().find({}, {"_id": 0}))
        srv_records = list(get_members_server_col().find({}, {"_id": 0}))

        game_file = discord.File(
            io.BytesIO(json.dumps(game_records, ensure_ascii=False, indent=2).encode("utf-8")),
            filename="members_game.json"
        )
        srv_file = discord.File(
            io.BytesIO(json.dumps(srv_records, ensure_ascii=False, indent=2).encode("utf-8")),
            filename="members_server.json"
        )

        try:
            await interaction.user.send(files=[game_file, srv_file])
            await interaction.followup.send("✅ Файлы отправлены в лс", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Не удалось отправить в лс — открой личные сообщения", ephemeral=True)


# ───────────────────────────────────────────────
# КОГ
# ───────────────────────────────────────────────

class MembersCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(DatabasePanelView())

    @discord.app_commands.command(name="write", description="Одноразовая заливка начальной базы участников")
    async def write_base(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        records = [
            {"uid": 1, "discord_username": "kurkk1", "discord_id": "852497017800097792", "full_name": "kuroki_antisocial", "static": "185686", "note": "-"},
            {"uid": 2, "discord_username": "s1lzorzzz", "discord_id": "588037655305125918", "full_name": "-", "static": "-", "note": "не знаю"},
            {"uid": 3, "discord_username": "femtochkaumer", "discord_id": "678225170682609685", "full_name": "graffuckla_antiscl", "static": "133371", "note": "-"},
            {"uid": 4, "discord_username": "qwkrill", "discord_id": "571253851580268554", "full_name": "-", "static": "-", "note": "не знаю"},
            {"uid": 5, "discord_username": "yumekomidari", "discord_id": "1379540005176872980", "full_name": "milka_antisocial", "static": "4599", "note": "-"},
            {"uid": 6, "discord_username": "ded3862", "discord_id": "739781664661700689", "full_name": "sodju_antisocial", "static": "48745", "note": "-"},
            {"uid": 7, "discord_username": "daic1k", "discord_id": "872451801302040617", "full_name": "aegis_antisocial", "static": "17266", "note": "-"},
            {"uid": 8, "discord_username": "akumara000_1", "discord_id": "1503779885137530981", "full_name": "akumara_antisocial", "static": "186001", "note": "-"},
            {"uid": 9, "discord_username": "alhimik.", "discord_id": "710335782140903435", "full_name": "-", "static": "-", "note": "не хочет играть"},
            {"uid": 10, "discord_username": "apokemons", "discord_id": "948539754478174228", "full_name": "youto_antiscl", "static": "167237", "note": "-"},
            {"uid": 11, "discord_username": "arsik_716", "discord_id": "771828343011409930", "full_name": "-", "static": "-", "note": "не хочет играть"},
            {"uid": 12, "discord_username": "idnhf", "discord_id": "740412410866696302", "full_name": "ataract_antisocial", "static": "138587", "note": "-"},
            {"uid": 13, "discord_username": "bat9boom", "discord_id": "1093953460371738837", "full_name": "-", "static": "-", "note": "не живой"},
            {"uid": 14, "discord_username": "q_tu", "discord_id": "593760280748621836", "full_name": "black_antisocial", "static": "38596", "note": "-"},
            {"uid": 15, "discord_username": "buivolcore", "discord_id": "564711604617347073", "full_name": "-", "static": "-", "note": "чисто плюсом"},
            {"uid": 16, "discord_username": "shinka100", "discord_id": "1140539622997372948", "full_name": "codeine_antiscl", "static": "125650", "note": "-"},
            {"uid": 17, "discord_username": "danielo909", "discord_id": "580019096683413514", "full_name": "-", "static": "-", "note": "плюс гешки"},
            {"uid": 18, "discord_username": "dezolatorgucci", "discord_id": "872422901331152897", "full_name": "-", "static": "-", "note": "плюс гешки"},
            {"uid": 19, "discord_username": "bybitownerz", "discord_id": "1266270138387402814", "full_name": "-", "static": "-", "note": "друг принка хз что с ним"},
            {"uid": 20, "discord_username": "umbra4xx", "discord_id": "886322342433812540", "full_name": "dollar_currency", "static": "17606", "note": "-"},
            {"uid": 21, "discord_username": "nxkturn3", "discord_id": "1081467052377784392", "full_name": "dreams_hunter", "static": "99847", "note": "-"},
            {"uid": 22, "discord_username": ".comkaa", "discord_id": "988114981872631888", "full_name": "jabeb_winline", "static": "1440", "note": "-"},
            {"uid": 23, "discord_username": "sundulb", "discord_id": "331065359626928128", "full_name": "geshka_vex", "static": "22093", "note": "-"},
            {"uid": 24, "discord_username": "bandit_of_venice", "discord_id": "295931615375523840", "full_name": "-", "static": "-", "note": "плюс гешки"},
            {"uid": 25, "discord_username": "sellamnp", "discord_id": "1007535281924227193", "full_name": "harukaze_antisocial", "static": "186404", "note": "-"},
            {"uid": 26, "discord_username": "vccxcc", "discord_id": "535039074193506304", "full_name": "-", "static": "-", "note": "не знаю"},
            {"uid": 27, "discord_username": "trybles", "discord_id": "374213478455443458", "full_name": "-", "static": "-", "note": "плюс гешки"},
            {"uid": 28, "discord_username": "ilyashka0", "discord_id": "913102464654389380", "full_name": "itadori_talentless", "static": "15468", "note": "-"},
            {"uid": 29, "discord_username": "fxuckcute", "discord_id": "898969187324686347", "full_name": "-", "static": "-", "note": "норм должен зайти"},
            {"uid": 30, "discord_username": "kmspokuni_", "discord_id": "649504229928468502", "full_name": "jonatan_antisocial", "static": "186277", "note": "-"},
            {"uid": 31, "discord_username": "psiqq", "discord_id": "473532385338589186", "full_name": "-", "static": "-", "note": "плюс гешки"},
            {"uid": 32, "discord_username": "kageshisa_", "discord_id": "1428289234770464799", "full_name": "kagesha_antisocial", "static": "7983", "note": "-"},
            {"uid": 33, "discord_username": "kiss812", "discord_id": "661211279448604704", "full_name": "karamelka_antiscl", "static": "19531", "note": "-"},
            {"uid": 34, "discord_username": ".kazurai", "discord_id": "1307296722786975784", "full_name": "kazurai_antisocial", "static": "27399", "note": "-"},
            {"uid": 35, "discord_username": "twizzy911", "discord_id": "641930694758760449", "full_name": "-", "static": "-", "note": "плюс гешки возможно"},
            {"uid": 36, "discord_username": "siniy3633", "discord_id": "564402051614900224", "full_name": "kirya_antisocial", "static": "187522", "note": "-"},
            {"uid": 37, "discord_username": ".kisesex", "discord_id": "932232775866654801", "full_name": "-", "static": "-", "note": "не знаю"},
            {"uid": 38, "discord_username": "kodi_s7", "discord_id": "917331466369781820", "full_name": "kodi_antisocial", "static": "89296", "note": "-"},
            {"uid": 39, "discord_username": "krunya1337", "discord_id": "491987704456675329", "full_name": "krunya_antisocial", "static": "186333", "note": "-"},
            {"uid": 40, "discord_username": "asd000_00", "discord_id": "1485676423250313226", "full_name": "light_antisocial", "static": "185688", "note": "-"},
            {"uid": 41, "discord_username": "xm1shanya", "discord_id": "1085812699893612594", "full_name": "-", "static": "-", "note": "плюс гешки"},
            {"uid": 42, "discord_username": "a6i6ok", "discord_id": "934500241087017060", "full_name": "maestro_antisocial", "static": "186911", "note": "-"},
            {"uid": 43, "discord_username": "maurizio222", "discord_id": "393045330037440518", "full_name": "-", "static": "-", "note": "плюс гешки"},
            {"uid": 44, "discord_username": ".prekracno", "discord_id": "706541114336739438", "full_name": "maximka_cortez", "static": "17155", "note": "-"},
            {"uid": 45, "discord_username": "bladina2015_53659", "discord_id": "1486790238750900314", "full_name": "-", "static": "-", "note": "не знаю"},
            {"uid": 46, "discord_username": "temamops", "discord_id": "1423646491586596975", "full_name": "-", "static": "-", "note": "euro_violence возможно"},
            {"uid": 47, "discord_username": "k1dutzu", "discord_id": "1224333734787022869", "full_name": "-", "static": "-", "note": "плюс гешки"},
            {"uid": 48, "discord_username": "obito52", "discord_id": "1244067170976010305", "full_name": "-", "static": "-", "note": "не заинвайчен"},
            {"uid": 49, "discord_username": "pulll123", "discord_id": "293034161999183874", "full_name": "-", "static": "-", "note": "тип харуказе возможно"},
            {"uid": 50, "discord_username": "pizdabank", "discord_id": "1344373504828768286", "full_name": "-", "static": "-", "note": "еблан"},
            {"uid": 51, "discord_username": "52mogila", "discord_id": "536041095549812757", "full_name": "prince_meersalz", "static": "1235", "note": "-"},
            {"uid": 52, "discord_username": "hanma48", "discord_id": "730790319351660604", "full_name": "-", "static": "-", "note": "не знаю заинвайтится"},
            {"uid": 53, "discord_username": "sajiha", "discord_id": "679553785059606550", "full_name": "sajiha_antisocial", "static": "190531", "note": "-"},
            {"uid": 54, "discord_username": "dreamer6478", "discord_id": "300157185634074637", "full_name": "-", "static": "-", "note": "плюс гешки"},
            {"uid": 55, "discord_username": "_takashik_", "discord_id": "670602580715634701", "full_name": "takashi_antisocial", "static": "20484", "note": "афк кент аегиса"},
            {"uid": 56, "discord_username": "takura1337", "discord_id": "946736184103673876", "full_name": "takura_blade", "static": "188980", "note": "-"},
            {"uid": 57, "discord_username": "theonewhoscared", "discord_id": "548567035615920148", "full_name": "-", "static": "-", "note": "не знаю"},
            {"uid": 58, "discord_username": "vadoskiy_", "discord_id": "1193519073475923978", "full_name": "vadoha_antisocial", "static": "187041", "note": "-"},
            {"uid": 59, "discord_username": "verolove00", "discord_id": "927174854416244756", "full_name": "-", "static": "-", "note": "плюс гешки"},
            {"uid": 60, "discord_username": "visaro1337", "discord_id": "617785505626038282", "full_name": "visaro_antisocial", "static": "187574", "note": "-"},
            {"uid": 61, "discord_username": "vorobey_kz", "discord_id": "755724484234674196", "full_name": "-", "static": "-", "note": "возможно не войдёт"},
            {"uid": 62, "discord_username": "yagami1404", "discord_id": "884836584609521694", "full_name": "yagami_antisocial", "static": "190476", "note": "-"},
            {"uid": 63, "discord_username": "alekfrosttt", "discord_id": "549295401000443904", "full_name": "frost_antisocial", "static": "186735", "note": "-"},
            {"uid": 64, "discord_username": "uxorious__", "discord_id": "1001105016558796941", "full_name": "uxorious_antisocial", "static": "190404", "note": "-"},
        ]

        col = get_members_game_col()
        inserted = 0
        skipped = 0
        for rec in records:
            if col.find_one({"discord_id": rec["discord_id"]}):
                skipped += 1
                continue
            col.insert_one(rec)
            inserted += 1

        # Выставляем счётчик uid
        max_uid = max(r["uid"] for r in records)
        get_db()["counters"].update_one(
            {"_id": "members_uid_counter"},
            {"$set": {"value": max_uid}},
            upsert=True
        )

        await interaction.followup.send(
            f"✅ Заливка завершена: добавлено {inserted}, пропущено {skipped}", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MembersCog(bot))
