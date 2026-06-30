import re
import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View

from config import (
    VACATION_CHANNEL_ID, VACATION_ROLE_ID,
    VACATION_REQUESTS_ID, VACATION_MOD_ROLES
)
from database import get_vacation_collection


def has_vacation_mod_role(member: discord.Member) -> bool:
    return any(role.id in VACATION_MOD_ROLES for role in member.roles)


# ───────────────────────────────────────────────
# UI
# ───────────────────────────────────────────────

class VacationModal(Modal, title="Заявка на отпуск"):
    until = TextInput(
        label="До какого отпуск?",
        placeholder="например: 25 июня",
        style=discord.TextStyle.short,
        min_length=1, max_length=100
    )
    reason = TextInput(
        label="Причина отпуска",
        placeholder="укажи причину",
        style=discord.TextStyle.paragraph,
        min_length=1, max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        requests_channel = guild.get_channel(VACATION_REQUESTS_ID)
        if not requests_channel:
            await interaction.followup.send("канал для заявок не найден", ephemeral=True)
            return

        vacation_role = guild.get_role(VACATION_ROLE_ID)
        if vacation_role and vacation_role in interaction.user.roles:
            await interaction.followup.send("ты уже в отпуске", ephemeral=True)
            return

        embed = discord.Embed(title="Новая заявка на отпуск", color=0x1ABC9C)
        embed.add_field(name="Участник", value=interaction.user.mention, inline=False)
        embed.add_field(name="До какого", value=self.until.value, inline=True)
        embed.add_field(name="Причина", value=self.reason.value, inline=False)
        embed.timestamp = discord.utils.utcnow()

        view = VacationApproveView(applicant_id=interaction.user.id)
        await requests_channel.send(embed=embed, view=view)

        await interaction.followup.send("заявка отправлена, ожидай решения", ephemeral=True)


class VacationApproveView(View):
    def __init__(self, applicant_id: int = None):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id

    def _get_applicant_id_from_embed(self, embed: discord.Embed):
        for field in embed.fields:
            if field.name == "Участник":
                match = re.search(r"\d{17,20}", field.value)
                if match:
                    return int(match.group())
        return None

    @discord.ui.button(label="✅ Принять", style=discord.ButtonStyle.green, custom_id="vacation_accept")
    async def vacation_accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_vacation_mod_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        await interaction.response.defer()

        guild = interaction.guild
        applicant_id = self._get_applicant_id_from_embed(interaction.message.embeds[0])
        if not applicant_id:
            await interaction.followup.send("не удалось найти участника", ephemeral=True)
            return

        applicant = guild.get_member(applicant_id)
        if not applicant:
            try:
                applicant = await guild.fetch_member(applicant_id)
            except Exception:
                await interaction.followup.send("участник не найден на сервере", ephemeral=True)
                return

        vacation_role = guild.get_role(VACATION_ROLE_ID)

        # Сохраняем роли
        roles_to_save = [r for r in applicant.roles if r.id != guild.default_role.id and r.id != VACATION_ROLE_ID]
        role_ids = [r.id for r in roles_to_save]

        try:
            col = get_vacation_collection()
            col.update_one(
                {"user_id": str(applicant_id)},
                {"$set": {"user_id": str(applicant_id), "saved_roles": role_ids}},
                upsert=True
            )
        except Exception as e:
            print(f"[ERROR] MongoDB vacation: {e}")

        try:
            if roles_to_save:
                await applicant.remove_roles(*roles_to_save, reason="отпуск")
        except Exception as e:
            print(f"[ERROR] снять роли: {e}")

        try:
            if vacation_role:
                await applicant.add_roles(vacation_role, reason="отпуск принят")
        except Exception as e:
            print(f"[ERROR] выдать отпускную роль: {e}")

        try:
            await applicant.send("ваша заявка на отпуск одобрена")
        except discord.Forbidden:
            pass

        old_embed = interaction.message.embeds[0]
        new_embed = discord.Embed(title=old_embed.title, color=0x2ecc71)
        for field in old_embed.fields:
            new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
        new_embed.add_field(name="Решение", value=f"✅ **Одобрена** модератором {interaction.user.mention}", inline=False)
        new_embed.timestamp = discord.utils.utcnow()
        await interaction.message.edit(embed=new_embed, view=None)

    @discord.ui.button(label="❌ Отклонить", style=discord.ButtonStyle.red, custom_id="vacation_reject")
    async def vacation_reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_vacation_mod_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        await interaction.response.defer()

        guild = interaction.guild
        applicant_id = self._get_applicant_id_from_embed(interaction.message.embeds[0])
        if not applicant_id:
            await interaction.followup.send("не удалось найти участника", ephemeral=True)
            return

        applicant = guild.get_member(applicant_id)
        if not applicant:
            try:
                applicant = await guild.fetch_member(applicant_id)
            except Exception:
                applicant = None  # участник вышел с сервера — всё равно позволяем отклонить

        if applicant:
            try:
                await applicant.send("ваша заявка на отпуск отклонена")
            except discord.Forbidden:
                pass

        old_embed = interaction.message.embeds[0]
        new_embed = discord.Embed(title=old_embed.title, color=0xff4444)
        for field in old_embed.fields:
            new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
        new_embed.add_field(name="Решение", value=f"❌ **Отклонена** модератором {interaction.user.mention}", inline=False)
        new_embed.timestamp = discord.utils.utcnow()
        await interaction.message.edit(embed=new_embed, view=None)


class VacationPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🏖️ Взять отпуск", style=discord.ButtonStyle.green, custom_id="vacation_take")
    async def vacation_take(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VacationModal())

    @discord.ui.button(label="🔙 Выйти из отпуска", style=discord.ButtonStyle.grey, custom_id="vacation_return")
    async def vacation_return(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        vacation_role = guild.get_role(VACATION_ROLE_ID)

        if vacation_role not in interaction.user.roles:
            await interaction.followup.send("ты не в отпуске", ephemeral=True)
            return

        try:
            col = get_vacation_collection()
            doc = col.find_one({"user_id": str(interaction.user.id)})
        except Exception as e:
            await interaction.followup.send("ошибка базы данных", ephemeral=True)
            print(f"[ERROR] MongoDB vacation: {e}")
            return

        try:
            await interaction.user.remove_roles(vacation_role, reason="выход из отпуска")
        except Exception as e:
            print(f"[ERROR] снять отпускную роль: {e}")

        if doc and doc.get("saved_roles"):
            roles_to_restore = [guild.get_role(rid) for rid in doc["saved_roles"]]
            roles_to_restore = [r for r in roles_to_restore if r]
            if roles_to_restore:
                try:
                    await interaction.user.add_roles(*roles_to_restore, reason="выход из отпуска")
                except Exception as e:
                    print(f"[ERROR] вернуть роли: {e}")

        try:
            col.delete_one({"user_id": str(interaction.user.id)})
        except Exception as e:
            print(f"[ERROR] MongoDB vacation delete: {e}")

        await interaction.followup.send("ты вышел из отпуска, роли возвращены", ephemeral=True)


def build_vacation_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🏖️ Заявка на отпуск",
        description=(
            "Вы можете подать заявку на отпуск если не будете играть какое-то время по каким-угодно причинам.\n\n"
            f"При одобрении вам будут сняты все роли и выдана <@&{VACATION_ROLE_ID}>, "
            "в любой момент вы можете зайти в этот канал, вернуться из отпуска и вам вернут все роли. "
            "В форме нужно будет указать срок и причину по которой будете отсутствовать"
        ),
        color=0x1ABC9C
    )
    return embed


# ───────────────────────────────────────────────
# Ког
# ───────────────────────────────────────────────

class VacationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(VacationPanelView())
        bot.add_view(VacationApproveView())


async def setup(bot: commands.Bot):
    await bot.add_cog(VacationCog(bot))
