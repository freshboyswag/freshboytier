import asyncio
import discord
from discord.ext import commands

from config import VOICE_LOG_CHANNEL_ID
from database import is_log_enabled


class VoiceLogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ───────────────────────────────────────────────
    # Хелперы поиска исполнителя (Discord API не даёт
    # target для member_move / member_disconnect —
    # это официальное ограничение самого Discord)
    # ───────────────────────────────────────────────

    async def _find_recent_disconnect_actor(self, guild: discord.Guild):
        # Несколько попыток с интервалом — audit log Discord иногда появляется с задержкой
        for attempt in range(4):
            await asyncio.sleep(1.5 if attempt == 0 else 2)
            try:
                async for entry in guild.audit_logs(limit=3, action=discord.AuditLogAction.member_disconnect):
                    if (discord.utils.utcnow() - entry.created_at).total_seconds() < 12:
                        return entry.user
            except Exception as e:
                print(f"[ERROR] audit log disconnect lookup (попытка {attempt + 1}): {e}")
                return None
        return None

    async def _find_recent_move_actor(self, guild: discord.Guild, target_channel_id: int):
        for attempt in range(4):
            await asyncio.sleep(1.5 if attempt == 0 else 2)
            try:
                async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.member_move):
                    if (discord.utils.utcnow() - entry.created_at).total_seconds() < 12:
                        channel = getattr(entry.extra, "channel", None)
                        if channel and channel.id == target_channel_id:
                            return entry.user
            except Exception as e:
                print(f"[ERROR] audit log move lookup (попытка {attempt + 1}): {e}")
                return None
        return None

    # ───────────────────────────────────────────────
    # Основные события войса
    # ───────────────────────────────────────────────

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

        # Вышел из войса полностью — либо сам, либо кикнут модератором
        if before.channel is not None and after.channel is None:
            kicked_by = await self._find_recent_disconnect_actor(guild)
            embed = discord.Embed(color=0xff4444)
            if kicked_by:
                embed.description = f"⛔ {kicked_by.mention} выкинул {member.mention} из **{before.channel.name}**"
            else:
                embed.description = f"🔴 {member.mention} вышел из **{before.channel.name}**"
            embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=embed)
            return

        # Переход между каналами — сам или мув модератором
        if before.channel is not None and after.channel is not None and before.channel != after.channel:
            mover = await self._find_recent_move_actor(guild, after.channel.id)
            embed = discord.Embed(color=0x3498db)
            if mover:
                embed.description = f"➡️ {mover.mention} переместил {member.mention} в **{after.channel.name}**"
            else:
                embed.description = f"🔄 {member.mention} перешёл из **{before.channel.name}** в **{after.channel.name}**"
            embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=embed)

    # ───────────────────────────────────────────────
    # Мут / деф сервером — target резолвится корректно,
    # это обычный member_update, не bulk-действие
    # ───────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        if not is_log_enabled("voice"):
            return

        if entry.action != discord.AuditLogAction.member_update:
            return

        guild = entry.guild
        log_channel = guild.get_channel(VOICE_LOG_CHANNEL_ID)
        if not log_channel:
            return

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
