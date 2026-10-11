"""
Единый файл всех логов бота.

Типы логов и их ключи для is_log_enabled()/set_log_enabled() (переключаются через админ-панель):
  "voice"       — вход/выход/переход/мут/деф в войсе
  "joins"       — вход/выход участников с сервера
  "roles"       — выдача/снятие ролей
  "names"       — смена ника и юзернейма
  "messages"    — редактирование/удаление сообщений
  "moderation"  — баны/кики

Сайлент лив логи (SILENT_LEAVE_*) работают отдельно от тоггла "joins" и не отключаются через панель.
"""

import time
import asyncio
import discord
from discord.ext import commands

from config import (
    VOICE_LOG_CHANNEL_ID, MEMBER_LOG_CHANNEL_ID,
    SILENT_LEAVE_LOG_CHANNEL_ID, SILENT_LEAVE_CHECK_ROLES,
    ROLE_LOGS_CHANNEL_ID, NAME_LOGS_CHANNEL_ID,
    MESSAGE_LOGS_CHANNEL_ID, MODERATION_LOGS_CHANNEL_ID,
)
from database import get_db, is_log_enabled


class LogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._recent_voice_events = {}

    # ═══════════════════════════════════════════════
    # ВОЙС-ЛОГИ
    # ═══════════════════════════════════════════════

    def _is_duplicate_voice(self, member_id: int, before_id, after_id) -> bool:
        key = (member_id, before_id, after_id)
        now = time.monotonic()
        last = self._recent_voice_events.get(key)
        self._recent_voice_events[key] = now
        self._recent_voice_events = {k: v for k, v in self._recent_voice_events.items() if now - v < 15}
        return last is not None and (now - last) < 4

    async def _find_recent_disconnect_actor(self, guild: discord.Guild):
        for attempt in range(4):
            await asyncio.sleep(1.5 if attempt == 0 else 2)
            try:
                async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.member_disconnect):
                    if (discord.utils.utcnow() - entry.created_at).total_seconds() < 15:
                        return entry.user
            except Exception as e:
                print(f"[ERROR] audit log disconnect lookup (попытка {attempt + 1}): {e}")
                continue
        return None

    async def _find_recent_move_actor(self, guild: discord.Guild, target_channel_id: int):
        for attempt in range(4):
            await asyncio.sleep(1.5 if attempt == 0 else 2)
            try:
                async for entry in guild.audit_logs(limit=8, action=discord.AuditLogAction.member_move):
                    if (discord.utils.utcnow() - entry.created_at).total_seconds() < 15:
                        channel = getattr(entry.extra, "channel", None)
                        if channel and channel.id == target_channel_id:
                            return entry.user
            except Exception as e:
                print(f"[ERROR] audit log move lookup (попытка {attempt + 1}): {e}")
                continue
        return None

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not is_log_enabled("voice"):
            return

        before_id = before.channel.id if before.channel else None
        after_id = after.channel.id if after.channel else None
        if before_id == after_id:
            return
        if self._is_duplicate_voice(member.id, before_id, after_id):
            return

        guild = member.guild
        log_channel = guild.get_channel(VOICE_LOG_CHANNEL_ID)
        if not log_channel:
            return

        if before.channel is None and after.channel is not None:
            embed = discord.Embed(color=0x2ecc71)
            embed.description = f"🟢 {member.mention} зашёл в **{after.channel.name}**"
            embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=embed)
            return

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

        if before.channel is not None and after.channel is not None and before.channel != after.channel:
            mover = await self._find_recent_move_actor(guild, after.channel.id)
            embed = discord.Embed(color=0x3498db)
            if mover:
                embed.description = f"➡️ {mover.mention} переместил {member.mention} в **{after.channel.name}**"
            else:
                embed.description = f"🔄 {member.mention} перешёл из **{before.channel.name}** в **{after.channel.name}**"
            embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=embed)

    # ═══════════════════════════════════════════════
    # ВХОД / ВЫХОД С СЕРВЕРА
    # ═══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not is_log_enabled("joins"):
            return

        channel = member.guild.get_channel(MEMBER_LOG_CHANNEL_ID)
        if not channel:
            return

        embed = discord.Embed(color=0x2ecc71)
        embed.set_author(name=f"{member.display_name} зашёл на сервер", icon_url=member.display_avatar.url)
        embed.add_field(name="Ник", value=member.display_name, inline=True)
        embed.add_field(name="Упоминание", value=member.mention, inline=True)
        embed.add_field(name="Юзернейм", value=f"@{member.name}", inline=True)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Аккаунт создан", value=discord.utils.format_dt(member.created_at, style="D"), inline=True)
        embed.add_field(name="Зашёл", value=discord.utils.format_dt(discord.utils.utcnow(), style="f"), inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()

        try:
            db = get_db()
            db["member_joins"].update_one(
                {"user_id": str(member.id)},
                {"$set": {"user_id": str(member.id), "joined_at": discord.utils.utcnow().isoformat()}},
                upsert=True
            )
        except Exception as e:
            print(f"[ERROR] member join log: {e}")

        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        member_role_ids = {r.id for r in member.roles}
        matched_role_ids = member_role_ids & SILENT_LEAVE_CHECK_ROLES
        if matched_role_ids:
            leave_log_channel = member.guild.get_channel(SILENT_LEAVE_LOG_CHANNEL_ID)
            if leave_log_channel:
                matched_roles = [member.guild.get_role(rid) for rid in matched_role_ids]
                matched_names = ", ".join(r.name for r in matched_roles if r)
                embed = discord.Embed(color=0xff4444)
                embed.description = (
                    f"⚠️ {member.mention} (`{member.name}`, `{member.id}`) вышел с сервера, "
                    f"имея роли: **{matched_names}**"
                )
                embed.timestamp = discord.utils.utcnow()
                try:
                    await leave_log_channel.send(embed=embed)
                except Exception as e:
                    print(f"[ERROR] leave roles log: {e}")

        if not is_log_enabled("joins"):
            return

        channel = member.guild.get_channel(MEMBER_LOG_CHANNEL_ID)
        if not channel:
            return

        embed = discord.Embed(color=0xff4444)
        embed.set_author(name=f"{member.display_name} вышел с сервера", icon_url=member.display_avatar.url)
        embed.add_field(name="Ник", value=member.display_name, inline=True)
        embed.add_field(name="Упоминание", value=member.mention, inline=True)
        embed.add_field(name="Юзернейм", value=f"@{member.name}", inline=True)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Аккаунт создан", value=discord.utils.format_dt(member.created_at, style="D"), inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)

        try:
            db = get_db()
            doc = db["member_joins"].find_one({"user_id": str(member.id)})
            if doc and doc.get("joined_at"):
                joined_at = discord.utils.parse_time(doc["joined_at"])
                now = discord.utils.utcnow()
                delta = now - joined_at
                days = delta.days
                hours = delta.seconds // 3600
                embed.add_field(name="Зашёл на сервер", value=discord.utils.format_dt(joined_at, style="f"), inline=True)
                embed.add_field(name="Пробыл", value=f"{days} дн. {hours} ч.", inline=True)
                db["member_joins"].delete_one({"user_id": str(member.id)})
        except Exception as e:
            print(f"[ERROR] member remove log: {e}")

        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed)

    # ═══════════════════════════════════════════════
    # СМЕНА НИКА / ЮЗЕРНЕЙМА
    # ═══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not is_log_enabled("names"):
            return
        if before.nick == after.nick:
            return

        channel = after.guild.get_channel(NAME_LOGS_CHANNEL_ID)
        if not channel:
            return

        embed = discord.Embed(color=0xf1c40f)
        embed.description = (
            f"✏️ {after.mention} сменил ник:\n"
            f"**{before.nick or before.name}** → **{after.nick or after.name}**"
        )
        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        if not is_log_enabled("names"):
            return
        if before.name == after.name and before.global_name == after.global_name:
            return

        for guild in self.bot.guilds:
            member = guild.get_member(after.id)
            if not member:
                continue
            channel = guild.get_channel(NAME_LOGS_CHANNEL_ID)
            if not channel:
                continue

            embed = discord.Embed(color=0xf1c40f)
            if before.name != after.name:
                embed.description = f"✏️ {after.mention} сменил юзернейм:\n**@{before.name}** → **@{after.name}**"
            else:
                embed.description = (
                    f"✏️ {after.mention} сменил отображаемое имя:\n"
                    f"**{before.global_name or before.name}** → **{after.global_name or after.name}**"
                )
            embed.timestamp = discord.utils.utcnow()
            await channel.send(embed=embed)

    # ═══════════════════════════════════════════════
    # РОЛИ / БАНЫ-КИКИ / МУТ-ДЕФ (через audit log)
    # ═══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        guild = entry.guild

        if entry.action == discord.AuditLogAction.member_role_update and is_log_enabled("roles"):
            channel = guild.get_channel(ROLE_LOGS_CHANNEL_ID)
            if channel and entry.target:
                added = list(getattr(entry.after, "roles", []) or [])
                removed = list(getattr(entry.before, "roles", []) or [])
                lines = []
                if added:
                    lines.append("➕ выдано: " + ", ".join(r.mention for r in added))
                if removed:
                    lines.append("➖ снято: " + ", ".join(r.mention for r in removed))
                if lines:
                    embed = discord.Embed(color=0x9b59b6)
                    embed.description = (
                        f"🎭 {entry.target.mention} — роли изменены ({entry.user.mention}):\n" + "\n".join(lines)
                    )
                    embed.timestamp = discord.utils.utcnow()
                    await channel.send(embed=embed)

        elif entry.action == discord.AuditLogAction.member_update and is_log_enabled("voice"):
            log_channel = guild.get_channel(VOICE_LOG_CHANNEL_ID)
            target = entry.target
            if log_channel and target:
                before_val, after_val = entry.before, entry.after
                if hasattr(before_val, "mute") and hasattr(after_val, "mute") and before_val.mute != after_val.mute:
                    embed = discord.Embed(color=0xe74c3c if after_val.mute else 0x2ecc71)
                    embed.description = (
                        f"🔇 {target.mention} был замучен {entry.user.mention}" if after_val.mute
                        else f"🔊 {target.mention} был размучен {entry.user.mention}"
                    )
                    embed.timestamp = discord.utils.utcnow()
                    await log_channel.send(embed=embed)

                if hasattr(before_val, "deaf") and hasattr(after_val, "deaf") and before_val.deaf != after_val.deaf:
                    embed = discord.Embed(color=0xe74c3c if after_val.deaf else 0x2ecc71)
                    embed.description = (
                        f"🎧 {entry.user.mention} выключил наушники {target.mention}" if after_val.deaf
                        else f"🎧 {entry.user.mention} включил наушники {target.mention}"
                    )
                    embed.timestamp = discord.utils.utcnow()
                    await log_channel.send(embed=embed)

        elif entry.action in (
            discord.AuditLogAction.ban, discord.AuditLogAction.unban, discord.AuditLogAction.kick
        ) and is_log_enabled("moderation"):
            channel = guild.get_channel(MODERATION_LOGS_CHANNEL_ID)
            if channel and entry.target:
                reason = entry.reason or "без причины"
                if entry.action == discord.AuditLogAction.ban:
                    embed = discord.Embed(color=0xe74c3c)
                    embed.description = f"🔨 {entry.user.mention} забанил {entry.target.mention}\nПричина: {reason}"
                elif entry.action == discord.AuditLogAction.unban:
                    embed = discord.Embed(color=0x2ecc71)
                    embed.description = f"🔓 {entry.user.mention} разбанил {entry.target.mention}"
                else:
                    embed = discord.Embed(color=0xe67e22)
                    embed.description = f"👢 {entry.user.mention} кикнул {entry.target.mention}\nПричина: {reason}"
                embed.timestamp = discord.utils.utcnow()
                await channel.send(embed=embed)

    # ═══════════════════════════════════════════════
    # РЕДАКТИРОВАНИЕ / УДАЛЕНИЕ СООБЩЕНИЙ
    # ═══════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not is_log_enabled("messages"):
            return
        if before.author.bot or before.content == after.content:
            return

        channel = before.guild.get_channel(MESSAGE_LOGS_CHANNEL_ID) if before.guild else None
        if not channel:
            return

        embed = discord.Embed(color=0x3498db)
        embed.description = f"📝 {before.author.mention} отредактировал сообщение в {before.channel.mention}"
        embed.add_field(name="До", value=(before.content or "*пусто*")[:1000], inline=False)
        embed.add_field(name="После", value=(after.content or "*пусто*")[:1000], inline=False)
        embed.add_field(name="Перейти", value=f"[ссылка]({after.jump_url})", inline=False)
        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not is_log_enabled("messages"):
            return
        if message.author.bot:
            return

        channel = message.guild.get_channel(MESSAGE_LOGS_CHANNEL_ID) if message.guild else None
        if not channel:
            return

        embed = discord.Embed(color=0xe74c3c)
        embed.description = f"🗑️ Сообщение от {message.author.mention} удалено в {message.channel.mention}"
        embed.add_field(name="Текст", value=(message.content or "*пусто / не в кэше*")[:1000], inline=False)
        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(LogsCog(bot))