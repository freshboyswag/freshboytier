import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View

from config import (
    TICKETS_CHANNEL_ID, RESULTS_CHANNEL_ID, LOGS_ACCEPTED_ID,
    LOGS_REJECTED_ID, TICKET_CATEGORY_ID,
    RECRUIT_ROLE_IDS, CALL_CHANNEL_ID, RESULTS_DM_CHANNEL_ID
)
from database import get_tickets_collection, is_recruitment_open


def has_recruit_role(member: discord.Member) -> bool:
    return any(role.id in RECRUIT_ROLE_IDS for role in member.roles)


# ───────────────────────────────────────────────
# UI
# ───────────────────────────────────────────────

class TicketModal(Modal, title="Подать заявку"):
    nick = TextInput(
        label="ник / статик / имя / возраст",
        placeholder="black / 133371 / мартин / 22",
        style=discord.TextStyle.short,
        min_length=1, max_length=100
    )
    online = TextInput(
        label="средний онлайн / прайм-тайм",
        placeholder="6-8 часов / 16:00 - 23:00",
        style=discord.TextStyle.short,
        min_length=1, max_length=100
    )
    history = TextInput(
        label="история семей",
        placeholder="alliance, faraday, cartel",
        style=discord.TextStyle.short,
        min_length=1, max_length=200
    )
    source = TextInput(
        label="откуда узнали о семье",
        placeholder="знакомый / маркет / игра",
        style=discord.TextStyle.short,
        min_length=1, max_length=200
    )
    clips = TextInput(
        label="откаты спешик/тяжка + сайга по 10 минут",
        placeholder="ссылки только с youtube/rutube/google drive",
        style=discord.TextStyle.paragraph,
        min_length=1, max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        category = guild.get_channel(TICKET_CATEGORY_ID)
        username = interaction.user.name

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for role_id in RECRUIT_ROLE_IDS:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(
            f"ticket-{username}",
            category=category,
            overwrites=overwrites
        )

        embed = discord.Embed(title="Новая заявка", color=0x2b2d31)
        embed.add_field(name="ник / статик / имя / возраст", value=self.nick.value, inline=False)
        embed.add_field(name="средний онлайн / прайм-тайм", value=self.online.value, inline=False)
        embed.add_field(name="история семей", value=self.history.value, inline=False)
        embed.add_field(name="откуда узнали о семье", value=self.source.value, inline=False)
        embed.add_field(name="откаты спешик/тяжка + сайга по 10 минут", value=self.clips.value, inline=False)
        embed.add_field(name="Пользователь", value=interaction.user.mention, inline=False)
        embed.add_field(name="Username", value=interaction.user.name, inline=True)
        embed.add_field(name="ID", value=str(interaction.user.id), inline=True)
        embed.timestamp = discord.utils.utcnow()

        mention_text = " ".join(f"<@&{role_id}>" for role_id in RECRUIT_ROLE_IDS)
        ticket_msg = await ticket_channel.send(
            content=mention_text,
            embed=embed,
            view=TicketActionsView(applicant_id=interaction.user.id)
        )

        try:
            col = get_tickets_collection()
            col.insert_one({
                "applicant_id": str(interaction.user.id),
                "applicant_name": interaction.user.name,
                "channel_id": str(ticket_channel.id),
                "ticket_msg_id": str(ticket_msg.id),
                "results_msg_id": None,
                "fields": {
                    "nick": self.nick.value,
                    "online": self.online.value,
                    "history": self.history.value,
                    "source": self.source.value,
                    "clips": self.clips.value,
                }
            })
        except Exception as e:
            print(f"[ERROR] MongoDB tickets: {e}")

        await interaction.followup.send(f"заявка подана! {ticket_channel.mention}", ephemeral=True)


class RejectModal(Modal, title="Причина отклонения"):
    reason = TextInput(
        label="Причина",
        placeholder="укажи причину отклонения",
        style=discord.TextStyle.short,
        min_length=1, max_length=300
    )

    def __init__(self, applicant, applicant_id: str, applicant_name: str, ticket_channel: discord.TextChannel, ticket_data: dict):
        super().__init__()
        self.applicant = applicant
        self.applicant_id = applicant_id
        self.applicant_name = applicant_name
        self.ticket_channel = ticket_channel
        self.ticket_data = ticket_data

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()

        guild = interaction.guild
        results_channel = guild.get_channel(RESULTS_CHANNEL_ID)
        logs_channel = guild.get_channel(LOGS_REJECTED_ID)

        results_msg_id = self.ticket_data.get("results_msg_id")
        if results_msg_id and results_channel:
            try:
                msg = await results_channel.fetch_message(int(results_msg_id))
                await msg.delete()
            except Exception:
                pass

        applicant_mention = self.applicant.mention if self.applicant else f"`{self.applicant_name}` (вышел с сервера)"

        embed = discord.Embed(color=0xff4444)
        embed.description = (
            f"Заявка пользователя {applicant_mention}\n\n"
            f"На вступление в семью была отклонена!\n"
            f"Причина: {self.reason.value}\n"
            f"Рассматривал заявку: {interaction.user.mention}"
        )
        if results_channel:
            await results_channel.send(embed=embed)

        if logs_channel:
            log_embed = discord.Embed(title="Заявка отклонена", color=0xff4444)
            log_embed.add_field(name="Пользователь", value=f"{applicant_mention} (`{self.applicant_name}`)", inline=False)
            fields = self.ticket_data.get("fields", {})
            log_embed.add_field(name="ник / статик / имя / возраст", value=fields.get("nick", "-"), inline=False)
            log_embed.add_field(name="средний онлайн / прайм-тайм", value=fields.get("online", "-"), inline=False)
            log_embed.add_field(name="история семей", value=fields.get("history", "-"), inline=False)
            log_embed.add_field(name="откуда узнали о семье", value=fields.get("source", "-"), inline=False)
            log_embed.add_field(name="откаты", value=fields.get("clips", "-"), inline=False)
            log_embed.add_field(name="Причина отклонения", value=self.reason.value, inline=False)
            log_embed.add_field(name="Рассматривал", value=interaction.user.mention, inline=False)
            log_embed.timestamp = discord.utils.utcnow()
            await logs_channel.send(embed=log_embed)

        try:
            col = get_tickets_collection()
            col.delete_one({"channel_id": str(self.ticket_channel.id)})
        except Exception as e:
            print(f"[ERROR] MongoDB: {e}")

        await self.ticket_channel.delete()


class TicketActionsView(View):
    def __init__(self, applicant_id: int = None):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id

    async def get_ticket_data(self, channel_id: str):
        try:
            col = get_tickets_collection()
            return col.find_one({"channel_id": channel_id})
        except Exception as e:
            print(f"[ERROR] MongoDB: {e}")
            return None

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, custom_id="ticket_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_recruit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        ticket_data = await self.get_ticket_data(str(interaction.channel.id))
        if not ticket_data:
            await interaction.response.send_message("данные тикета не найдены", ephemeral=True)
            return

        applicant = interaction.guild.get_member(int(ticket_data["applicant_id"]))
        if not applicant:
            try:
                applicant = await interaction.guild.fetch_member(int(ticket_data["applicant_id"]))
            except Exception:
                await interaction.response.send_message("пользователь не найден на сервере", ephemeral=True)
                return

        from cogs.members import AcceptGameDataModal
        await interaction.response.send_modal(
            AcceptGameDataModal(
                ticket_data=ticket_data,
                applicant=applicant,
                ticket_channel=interaction.channel
            )
        )

    @discord.ui.button(label="Взять на рассмотрение", style=discord.ButtonStyle.green, custom_id="ticket_review")
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_recruit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.channel.send(f"{interaction.user.mention} начал рассмотрение заявки")

    @discord.ui.button(label="Вызвать на обзвон", style=discord.ButtonStyle.green, custom_id="ticket_call")
    async def call(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_recruit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer()

        guild = interaction.guild
        ticket_data = await self.get_ticket_data(str(interaction.channel.id))
        if not ticket_data:
            await interaction.followup.send("данные тикета не найдены", ephemeral=True)
            return

        applicant = guild.get_member(int(ticket_data["applicant_id"]))
        results_channel = guild.get_channel(RESULTS_CHANNEL_ID)

        call_embed = discord.Embed(
            title="Вы были вызваны на обзвон",
            description=f"Зайдите в голосовой канал <#{CALL_CHANNEL_ID}> и ожидайте пока вас мувнут",
            color=0x1ABC9C
        )

        dm_sent = False
        if applicant:
            try:
                await applicant.send(embed=call_embed)
                dm_sent = True
            except discord.Forbidden:
                pass

        results_msg = None
        if not dm_sent and results_channel and applicant:
            results_msg = await results_channel.send(content=applicant.mention, embed=call_embed)

        if results_msg:
            try:
                col = get_tickets_collection()
                col.update_one(
                    {"channel_id": str(interaction.channel.id)},
                    {"$set": {"results_msg_id": str(results_msg.id)}}
                )
            except Exception as e:
                print(f"[ERROR] MongoDB: {e}")

        await interaction.followup.send("уведомление отправлено", ephemeral=True)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, custom_id="ticket_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_recruit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        guild = interaction.guild
        ticket_data = await self.get_ticket_data(str(interaction.channel.id))
        if not ticket_data:
            await interaction.response.send_message("данные тикета не найдены", ephemeral=True)
            return

        applicant = guild.get_member(int(ticket_data["applicant_id"]))
        if not applicant:
            try:
                applicant = await guild.fetch_member(int(ticket_data["applicant_id"]))
            except Exception:
                applicant = None  # участник вышел с сервера — всё равно позволяем отклонить

        await interaction.response.send_modal(
            RejectModal(
                applicant=applicant,
                applicant_id=ticket_data["applicant_id"],
                applicant_name=ticket_data.get("applicant_name", "?"),
                ticket_channel=interaction.channel,
                ticket_data=ticket_data
            )
        )


class TicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Выбери действие...",
        custom_id="submit_ticket_select",
        options=[
            discord.SelectOption(label="Подать заявку", value="submit_ticket", emoji="📩"),
        ]
    )
    async def submit_ticket(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not is_recruitment_open():
            await interaction.response.send_message(
                "❌ набор в семью сейчас закрыт, попробуй позже",
                ephemeral=True
            )
            return
        await interaction.response.send_modal(TicketModal())
        # сбрасываем визуально выбранный пункт селекта на исходном сообщении,
        # иначе после закрытия формы без отправки клиент не даёт выбрать тот же пункт снова
        try:
            await interaction.message.edit(view=TicketPanelView())
        except discord.HTTPException:
            pass


def build_ticket_panel_embed() -> discord.Embed:
    from database import get_panel_override
    override = get_panel_override("tickets")

    default_title = "👋 Путь в семью начинается здесь!"
    default_description = (
        f"Обычно приглашение на обзвон отправляется в личные сообщения, "
        f"если лс закрыт то оно придёт в канал <#{RESULTS_DM_CHANNEL_ID}>\n\n"
        "Все поля обязательны для заполнения\nЗаявки, заполненные не по форме, будут сразу закрыты\n"
        "Заявки без откатов будут закрыты, писать «покажу на демке», «кину позже» не нужно\n"
        "Откаты с гг должны быть не старше 2 недель\n"
        "Тикеты рассматриваются в течение 48 часов"
    )

    embed = discord.Embed(
        title=override["title"] if override else default_title,
        description=override["description"] if override else default_description,
        color=0x2b2d31
    )
    embed.set_image(url="https://media.discordapp.net/attachments/1523386915582378004/1523425736877281411/tickets.png?ex=6a4c1047&is=6a4abec7&hm=22b4f72a2e207e4af05c556d7a2ab723c7c47bfa0a6bfc78c5e39bb652c67a09&=&format=webp&quality=lossless&width=1303&height=977")
    return embed


# ───────────────────────────────────────────────
# Ког
# ───────────────────────────────────────────────

class TicketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(TicketPanelView())
        bot.add_view(TicketActionsView())


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketsCog(bot))
