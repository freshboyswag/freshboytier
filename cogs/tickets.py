import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View

from config import (
    TICKETS_CHANNEL_ID, RESULTS_CHANNEL_ID, LOGS_ACCEPTED_ID,
    LOGS_REJECTED_ID, TICKET_CATEGORY_ID, TICKET_VIEW_ROLE_ID,
    RECRUIT_ROLE_IDS, ACCEPT_ROLE_ID, CALL_CHANNEL_ID, RESULTS_DM_CHANNEL_ID
)
from database import get_tickets_collection


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
        ticket_view_role = guild.get_role(TICKET_VIEW_ROLE_ID)
        category = guild.get_channel(TICKET_CATEGORY_ID)
        username = interaction.user.name

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if ticket_view_role:
            overwrites[ticket_view_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

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

        mention_text = f"<@&{TICKET_VIEW_ROLE_ID}>"
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

    def __init__(self, applicant: discord.Member, ticket_channel: discord.TextChannel, ticket_data: dict):
        super().__init__()
        self.applicant = applicant
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

        embed = discord.Embed(color=0xff4444)
        embed.description = (
            f"Заявка пользователя {self.applicant.mention}\n\n"
            f"На вступление в семью была отклонена!\n"
            f"Причина: {self.reason.value}\n"
            f"Рассматривал заявку: {interaction.user.mention}"
        )
        if results_channel:
            await results_channel.send(embed=embed)

        if logs_channel:
            log_embed = discord.Embed(title="Заявка отклонена", color=0xff4444)
            log_embed.add_field(name="Пользователь", value=f"{self.applicant.mention} (`{self.applicant.name}`)", inline=False)
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

        await interaction.response.defer()

        guild = interaction.guild
        ticket_data = await self.get_ticket_data(str(interaction.channel.id))
        if not ticket_data:
            await interaction.followup.send("данные тикета не найдены", ephemeral=True)
            return

        applicant = guild.get_member(int(ticket_data["applicant_id"]))
        results_channel = guild.get_channel(RESULTS_CHANNEL_ID)
        logs_channel = guild.get_channel(LOGS_ACCEPTED_ID)

        results_msg_id = ticket_data.get("results_msg_id")
        if results_msg_id and results_channel:
            try:
                msg = await results_channel.fetch_message(int(results_msg_id))
                await msg.delete()
            except Exception:
                pass

        if applicant:
            accept_role = guild.get_role(ACCEPT_ROLE_ID)
            if accept_role:
                try:
                    await applicant.add_roles(accept_role)
                except Exception as e:
                    print(f"[ERROR] Не удалось выдать роль: {e}")

        embed = discord.Embed(color=0x2ecc71)
        embed.description = (
            f"Заявка пользователя {applicant.mention if applicant else ticket_data['applicant_name']}\n\n"
            f"На вступление в семью была принята! 🎉\n"
            f"Рассматривал заявку: {interaction.user.mention}"
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
            log_embed.add_field(name="Принял", value=interaction.user.mention, inline=False)
            log_embed.timestamp = discord.utils.utcnow()
            await logs_channel.send(embed=log_embed)

        try:
            col = get_tickets_collection()
            col.delete_one({"channel_id": str(interaction.channel.id)})
        except Exception as e:
            print(f"[ERROR] MongoDB: {e}")

        await interaction.channel.delete()

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
            await interaction.response.send_message("пользователь не найден", ephemeral=True)
            return

        await interaction.response.send_modal(
            RejectModal(
                applicant=applicant,
                ticket_channel=interaction.channel,
                ticket_data=ticket_data
            )
        )


class TicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Подать заявку", style=discord.ButtonStyle.blurple, custom_id="submit_ticket")
    async def submit_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal())


def build_ticket_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="👋 Путь в семью начинается здесь!",
        description=(
            f"Обычно приглашение на обзвон отправляется в личные сообщения, "
            f"если лс закрыт то оно придёт в канал <#{RESULTS_DM_CHANNEL_ID}>\n\n"
            "Все поля обязательны для заполнения\nЗаявки, заполненные не по форме, будут сразу закрыты\n"
            "Заявки без откатов будут закрыты, писать «покажу на демке», «кину позже» не нужно\n"
            "Откаты с гг должны быть не старше 2 недель\n"
            "Тикеты рассматриваются в течение 48 часов"
        ),
        color=0x2b2d31
    )
    embed.set_image(url="https://media.discordapp.net/attachments/1468989054451191840/1515329976151310376/photo_2026-06-13_15-20-03.jpg?ex=6a2e9c83&is=6a2d4b03&hm=6737177b88f5f1d677b6eb893b72a2d1691fb2da128946e5165160f23f2dda8c&=&format=webp&width=900&height=900")
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
