import asyncio
import discord
from discord.ext import commands

from config import VOICE_LOG_CHANNEL_ID
from database import is_log_enabled


class VoiceLogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not is_log_enabled("voice"):
            return

        guild = member.guild
        log_channel = guild.get_channel(VOICE_LOG_CHANNEL_ID)
        if not log_channel:
            return

        # Зашёл в канал
        if before.channel is None and after.channel is not None:
            embed = discord.Embed(color=0x2ecc71)
            embed.description = f"🟢 {member.mention} зашёл в **{after.channel.name}**"
            embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=embed)
            return

        # Вышел из канала
        if before.channel is not None and after.channel is None:
            embed = discord.Embed(color=0xff4444)
            embed.description = f"🔴 {member.mention} вышел из **{before.channel.name}**"
            embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=embed)
            return

        # Переход между каналами — проверяем был ли мув через аудит лог
        if before.channel is not None and after.channel is not None and before.channel != after.channel:
            await asyncio.sleep(1)
            try:
                async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.member_move):
                    if (discord.utils.utcnow() - entry.created_at).total_seconds() < 3:
                        return  # Был мув — on_audit_log_entry_create уже залогировал
            except Exception:
                pass
            # Сам перешёл
            embed = discord.Embed(color=0x3498db)
            embed.description = f"🔄 {member.mention} перешёл из **{before.channel.name}** в **{after.channel.name}**"
            embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        if not is_log_enabled("voice"):
            return

        guild = entry.guild
        log_channel = guild.get_channel(VOICE_LOG_CHANNEL_ID)
        if not log_channel:
            return

        # Мув участника
        if entry.action == discord.AuditLogAction.member_move:
            target = entry.target
            channel = entry.extra.channel if hasattr(entry, "extra") and entry.extra and hasattr(entry.extra, "channel") else None
            embed = discord.Embed(color=0x3498db)
            if channel:
                embed.description = f"➡️ {entry.user.mention} переместил {target.mention} в **{channel.name}**"
            else:
                embed.description = f"➡️ {entry.user.mention} переместил {target.mention} в другой канал"
            embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=embed)

        # Кик из войса
        elif entry.action == discord.AuditLogAction.member_disconnect:
            target = entry.target
            embed = discord.Embed(color=0xff4444)
            embed.description = f"⛔ {entry.user.mention} выкинул {target.mention} из голосового канала"
            embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=embed)

        # Мут / деф сервером
        elif entry.action == discord.AuditLogAction.member_update:
            target = entry.target
            if not target:
                return

            before_val = entry.before
            after_val = entry.after

            if hasattr(before_val, "mute") and hasattr(after_val, "mute") and before_val.mute != after_val.mute:
                embed = discord.Embed(color=0xe74c3c if after_val.mute else 0x2ecc71)
                if after_val.mute:
                    embed.description = f"🔇 {target.mention} был замучен {entry.user.mention}"
                else:
                    embed.description = f"🔊 {target.mention} был размучен {entry.user.mention}"
                embed.timestamp = discord.utils.utcnow()
                await log_channel.send(embed=embed)

            if hasattr(before_val, "deaf") and hasattr(after_val, "deaf") and before_val.deaf != after_val.deaf:
                embed = discord.Embed(color=0xe74c3c if after_val.deaf else 0x2ecc71)
                if after_val.deaf:
                    embed.description = f"🎧 {entry.user.mention} выключил наушники {target.mention}"
                else:
                    embed.description = f"🎧 {entry.user.mention} включил наушники {target.mention}"
                embed.timestamp = discord.utils.utcnow()
                await log_channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceLogsCog(bot))
