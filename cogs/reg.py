import discord
from discord.ext import commands
from discord.ui import View
from datetime import timedelta

from config import (
    REG_ADMIN_ROLES, CHECKMARK_EMOJI, MP_LOGS_CHANNEL_ID,
    MP_RETENTION_DAYS, TIER_ORDER, VOICE_CHECK_CHANNELS
)
from database import (
    get_reg_collection, get_next_mp_number,
    get_reg_data, get_reg_data_by_thread, save_reg_data
)


# ───────────────────────────────────────────────
# Хелперы
# ───────────────────────────────────────────────

def has_reg_admin_role(member: discord.Member) -> bool:
    return any(role.id in REG_ADMIN_ROLES for role in member.roles)

def get_tier_label(member: discord.Member) -> str:
    member_role_ids = {r.id for r in member.roles}
    for label, role_id in TIER_ORDER:
        if role_id is None:
            return label
        if role_id in member_role_ids:
            return label
    return "no tier"

def insert_by_tier(main_list: list, new_entry: dict) -> list:
    tier_order_labels = [label for label, _ in TIER_ORDER]
    new_tier = new_entry.get("tier", "no tier")
    new_tier_idx = tier_order_labels.index(new_tier) if new_tier in tier_order_labels else len(tier_order_labels)

    insert_pos = len(main_list)
    for i, u in enumerate(main_list):
        u_tier = u.get("tier", "no tier")
        u_tier_idx = tier_order_labels.index(u_tier) if u_tier in tier_order_labels else len(tier_order_labels)
        if u_tier_idx > new_tier_idx:
            insert_pos = i
            break

    main_list.insert(insert_pos, new_entry)
    return main_list


# ───────────────────────────────────────────────
# Эмбеды
# ───────────────────────────────────────────────

def build_reg_embed(data: dict) -> discord.Embed:
    max_slots = data["max_slots"]
    main_list = data["main_list"]
    title = data.get("title", "Список")
    mp_number = data.get("mp_number", "?")
    closed = data.get("closed", False)

    if closed:
        return discord.Embed(
            title=f"Мероприятие #{mp_number} закрыто",
            color=0xff4444
        )

    tier_groups = {label: [] for label, _ in TIER_ORDER}
    for entry in main_list:
        tier = entry.get("tier", "no tier")
        if tier not in tier_groups:
            tier = "no tier"
        tier_groups[tier].append(entry)

    lines = []
    counter = 1
    for label, _ in TIER_ORDER:
        members = tier_groups[label]
        if not members:
            continue
        lines.append(f"**{label}:**")
        for u in members:
            lines.append(f"\u00a0\u00a0{counter}. <@{u['id']}>")
            counter += 1

    description = f"Основной список {len(main_list)}/{max_slots}"
    body = "\n".join(lines) if lines else "пусто"

    embed = discord.Embed(title=title, description=description, color=0x1ABC9C)
    embed.add_field(name="\u200b", value=body, inline=False)
    return embed


def build_mp_log_embed(data: dict, closed: bool = False) -> discord.Embed:
    mp_number = data.get("mp_number", "?")
    title = data.get("title", "Список")
    max_slots = data.get("max_slots", "?")
    creator_id = data.get("creator_id")
    created_at = data.get("created_at")

    embed = discord.Embed(
        title=f"Мероприятие #{mp_number}",
        color=0xff4444 if closed else 0x2ecc71
    )
    embed.add_field(name="Название", value=title, inline=False)
    embed.add_field(name="Слотов", value=str(max_slots), inline=True)
    if creator_id:
        embed.add_field(name="Создал", value=f"<@{creator_id}>", inline=True)
    if created_at:
        try:
            dt = discord.utils.parse_time(created_at)
            embed.add_field(name="Дата создания", value=discord.utils.format_dt(dt, style="f"), inline=True)
        except Exception:
            pass
    if closed:
        closer_id = data.get("closer_id")
        closed_at = data.get("closed_at")
        if closer_id:
            embed.add_field(name="Закрыл", value=f"<@{closer_id}>", inline=True)
        if closed_at:
            try:
                dt = discord.utils.parse_time(closed_at)
                embed.add_field(name="Дата закрытия", value=discord.utils.format_dt(dt, style="f"), inline=True)
            except Exception:
                pass
    embed.timestamp = discord.utils.utcnow()
    return embed


# ───────────────────────────────────────────────
# Лог МП
# ───────────────────────────────────────────────

async def log_mp_event(bot: commands.Bot, data: dict, text: str):
    try:
        mp_log_thread_id = data.get("mp_log_thread_id")
        if not mp_log_thread_id:
            return
        thread = bot.get_channel(int(mp_log_thread_id))
        if not thread:
            thread = await bot.fetch_channel(int(mp_log_thread_id))
        if thread:
            await thread.send(text)
    except Exception as e:
        print(f"[ERROR] log_mp_event: {e}")


async def update_reg_embed(message: discord.Message, data: dict):
    try:
        embed = build_reg_embed(data)
        await message.edit(embed=embed, view=RegView())
    except Exception as e:
        print(f"[ERROR] update reg embed: {e}")


# ───────────────────────────────────────────────
# Проверка по войсу
# ───────────────────────────────────────────────

class VoiceChannelSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, reg_message_id: int, reg_data: dict, bot: commands.Bot):
        self.reg_message_id = reg_message_id
        self.reg_data = reg_data
        self.bot = bot

        options = []
        for ch_id in VOICE_CHECK_CHANNELS:
            ch = guild.get_channel(ch_id)
            if ch:
                options.append(discord.SelectOption(label=ch.name, value=str(ch.id)))

        if not options:
            options.append(discord.SelectOption(label="каналы не найдены", value="none"))

        super().__init__(placeholder="выберите голосовой канал...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if self.values[0] == "none":
            await interaction.followup.send("голосовые каналы не найдены", ephemeral=True)
            return

        voice_channel = interaction.guild.get_channel(int(self.values[0]))
        if not voice_channel:
            await interaction.followup.send("канал не найден", ephemeral=True)
            return

        members_in_voice = {str(m.id) for m in voice_channel.members}
        main_list = self.reg_data.get("main_list", [])

        in_voice = [u for u in main_list if u["id"] in members_in_voice]
        not_in_voice = [u for u in main_list if u["id"] not in members_in_voice]

        lines = [
            f"**Проверка по войсу: {voice_channel.name}**", "",
            f"**Присутствуют ({len(in_voice)}/{len(main_list)}):**"
        ]
        lines += ["✅ " + u["nick"] for u in in_voice] if in_voice else ["никого"]
        lines += ["", f"**Отсутствуют ({len(not_in_voice)}/{len(main_list)}):**"]
        lines += ["❌ " + u["nick"] for u in not_in_voice] if not_in_voice else ["все на месте"]

        await interaction.followup.send("\n".join(lines), ephemeral=True)

        # Короткий лог в ветку "плюсы"
        try:
            reg_channel = interaction.guild.get_channel(int(self.reg_data.get("channel_id", 0)))
            if reg_channel:
                reg_msg = await reg_channel.fetch_message(self.reg_message_id)
                if reg_msg and reg_msg.thread:
                    absent_mentions = " ".join(f"<@{u['id']}>" for u in not_in_voice)
                    absent_line = f"Отсутствуют: {absent_mentions}" if absent_mentions else "Все присутствуют"
                    await reg_msg.thread.send(
                        f"{interaction.user.display_name} провёл проверку по войсу {voice_channel.name}\n{absent_line}"
                    )
        except Exception as e:
            print(f"[ERROR] voice check log: {e}")

        # Полный лог в лог-ветку МП
        full_lines = [
            f"**{interaction.user.display_name} провёл проверку по войсу: {voice_channel.name}**", "",
            f"**Присутствуют ({len(in_voice)}/{len(main_list)}):**"
        ]
        full_lines += ["✅ " + u["nick"] for u in in_voice] if in_voice else ["никого"]
        full_lines += ["", f"**Отсутствуют ({len(not_in_voice)}/{len(main_list)}):**"]
        full_lines += ["❌ " + u["nick"] for u in not_in_voice] if not_in_voice else ["все на месте"]

        await log_mp_event(self.bot, self.reg_data, "\n".join(full_lines))

        absent_nicks = ", ".join(u["nick"] for u in not_in_voice)
        if absent_nicks:
            await log_mp_event(self.bot, self.reg_data, f"Отсутствуют и тегнуты в канал: {absent_nicks}")


class VoiceSelectView(View):
    def __init__(self, reg_message_id: int, reg_data: dict, bot: commands.Bot):
        super().__init__(timeout=30)
        self.reg_message_id = reg_message_id
        self.reg_data = reg_data
        self.bot = bot

    async def setup(self, guild: discord.Guild):
        self.add_item(VoiceChannelSelect(guild, self.reg_message_id, self.reg_data, self.bot))


# ───────────────────────────────────────────────
# Мод меню
# ───────────────────────────────────────────────

class ModMenuSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, reg_message: discord.Message, data: dict, bot: commands.Bot):
        self.reg_message = reg_message
        self.data = data
        self.guild = guild
        self.bot = bot

        closed = data.get("closed", False)
        if closed:
            options = [discord.SelectOption(label="ℹ️ Инфо об МП", value="info")]
        else:
            options = [
                discord.SelectOption(label="📋 Сформировать список", value="form"),
                discord.SelectOption(label="📣 Тегнуть список", value="tag"),
                discord.SelectOption(label="🎤 Проверка по войсу", value="voice"),
                discord.SelectOption(label="ℹ️ Инфо об МП", value="info"),
                discord.SelectOption(label="🔒 Закрыть МП", value="close"),
            ]
        super().__init__(placeholder="выберите действие...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if self.values[0] == "form":
            thread = self.reg_message.thread if hasattr(self.reg_message, "thread") and self.reg_message.thread else None
            if not thread:
                await interaction.followup.send("ветка не найдена", ephemeral=True)
                return

            valid_users = []
            async for msg in thread.history(limit=500, oldest_first=True):
                if msg.author.bot or "+" not in msg.content:
                    continue
                has_check = False
                for reaction in msg.reactions:
                    if str(reaction.emoji) == CHECKMARK_EMOJI:
                        async for user in reaction.users():
                            if not user.bot:
                                m = interaction.guild.get_member(user.id)
                                if m and has_reg_admin_role(m):
                                    has_check = True
                                    break
                    if has_check:
                        break
                if has_check:
                    uid = str(msg.author.id)
                    if not any(u["id"] == uid for u in valid_users):
                        member = interaction.guild.get_member(int(uid))
                        if not member:
                            try:
                                member = await interaction.guild.fetch_member(int(uid))
                            except Exception:
                                continue
                        tier = get_tier_label(member)
                        valid_users = insert_by_tier(valid_users, {"id": uid, "nick": member.display_name, "tier": tier})

            self.data["main_list"] = valid_users
            await save_reg_data(int(self.reg_message.id), {"main_list": valid_users})
            await update_reg_embed(self.reg_message, self.data)

            list_lines = [f"{interaction.user.display_name} сформировал список из {len(valid_users)} человек:"]
            for u in valid_users:
                list_lines.append(u["nick"])
            await thread.send("\n".join(list_lines))

            await interaction.followup.send(f"список сформирован: {len(valid_users)} участников", ephemeral=True)

        elif self.values[0] == "tag":
            main_list = self.data.get("main_list", [])
            if not main_list:
                await interaction.followup.send("список пуст", ephemeral=True)
                return
            mentions = " ".join(f"<@{u['id']}>" for u in main_list)
            thread = self.reg_message.thread if hasattr(self.reg_message, "thread") and self.reg_message.thread else None
            if thread:
                await thread.send(mentions)
                await interaction.followup.send("список тегнут в ветку", ephemeral=True)
            else:
                await interaction.followup.send(mentions)

        elif self.values[0] == "voice":
            view = VoiceSelectView(reg_message_id=self.reg_message.id, reg_data=self.data, bot=self.bot)
            await view.setup(interaction.guild)
            await interaction.followup.send("выберите голосовой канал для проверки:", view=view, ephemeral=True)

        elif self.values[0] == "info":
            mp_number = self.data.get("mp_number", "?")
            creator_id = self.data.get("creator_id")
            created_at = self.data.get("created_at")
            thread_link = self.data.get("mp_log_thread_link", "не найдена")

            lines = [f"**Мероприятие #{mp_number}**"]
            if creator_id:
                lines.append(f"Создал: <@{creator_id}>")
            if created_at:
                try:
                    dt = discord.utils.parse_time(created_at)
                    lines.append(f"Создано: {discord.utils.format_dt(dt, style='f')}")
                except Exception:
                    pass
            lines.append(f"Ветка с логами: {thread_link}")

            if self.data.get("closed"):
                closer_id = self.data.get("closer_id")
                closed_at = self.data.get("closed_at")
                if closer_id:
                    lines.append(f"Закрыл: <@{closer_id}>")
                if closed_at:
                    try:
                        dt = discord.utils.parse_time(closed_at)
                        lines.append(f"Закрыто: {discord.utils.format_dt(dt, style='f')}")
                    except Exception:
                        pass

            await interaction.followup.send("\n".join(lines), ephemeral=True)

        elif self.values[0] == "close":
            if self.data.get("closed"):
                await interaction.followup.send("МП уже закрыто", ephemeral=True)
                return

            closed_at_iso = discord.utils.utcnow().isoformat()
            self.data["closed"] = True
            self.data["closer_id"] = str(interaction.user.id)
            self.data["closed_at"] = closed_at_iso

            await save_reg_data(int(self.reg_message.id), {
                "closed": True,
                "closer_id": str(interaction.user.id),
                "closed_at": closed_at_iso
            })

            await update_reg_embed(self.reg_message, self.data)

            # Закрываем ветку "плюсы"
            try:
                thread = self.reg_message.thread if hasattr(self.reg_message, "thread") and self.reg_message.thread else None
                if thread:
                    await thread.send(f"{interaction.user.mention} завершил мероприятие")
                    await thread.edit(locked=True, archived=True)
            except Exception as e:
                print(f"[ERROR] close plus thread: {e}")

            # Итоговый список в лог-ветку МП
            main_list = self.data.get("main_list", [])
            final_lines = [f"Итоговый список перед закрытием ({len(main_list)} человек):"]
            final_lines += [u["nick"] for u in main_list]
            await log_mp_event(self.bot, self.data, "\n".join(final_lines))
            await log_mp_event(self.bot, self.data, f"Мероприятие закрыто {interaction.user.display_name}")

            # Закрываем лог-ветку
            try:
                mp_log_thread_id = self.data.get("mp_log_thread_id")
                if mp_log_thread_id:
                    log_thread = self.bot.get_channel(int(mp_log_thread_id))
                    if log_thread:
                        await log_thread.edit(locked=True, archived=True)
            except Exception as e:
                print(f"[ERROR] close mp log thread: {e}")

            # Обновляем эмбед в канале логов МП
            try:
                mp_log_msg_id = self.data.get("mp_log_message_id")
                if mp_log_msg_id:
                    log_channel = self.bot.get_channel(MP_LOGS_CHANNEL_ID)
                    if log_channel:
                        log_msg = await log_channel.fetch_message(int(mp_log_msg_id))
                        await log_msg.edit(embed=build_mp_log_embed(self.data, closed=True))
            except Exception as e:
                print(f"[ERROR] update mp log embed on close: {e}")

            await interaction.followup.send(f"Мероприятие #{self.data.get('mp_number')} закрыто", ephemeral=True)


class ModMenuView(View):
    def __init__(self, guild: discord.Guild, reg_message: discord.Message, data: dict, bot: commands.Bot):
        super().__init__(timeout=30)
        self.add_item(ModMenuSelect(guild, reg_message, data, bot))


class RegView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛠 Мод", style=discord.ButtonStyle.grey, custom_id="reg_mod_menu")
    async def reg_mod_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_reg_admin_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        data = await get_reg_data(interaction.message.id)
        if not data:
            await interaction.followup.send("список не найден", ephemeral=True)
            return

        bot = interaction.client
        view = ModMenuView(interaction.guild, interaction.message, data, bot)
        await interaction.followup.send("выберите действие:", view=view, ephemeral=True)


# ───────────────────────────────────────────────
# Ког
# ───────────────────────────────────────────────

class RegCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(RegView())
        self.bot.loop.create_task(self.mp_cleanup_loop())

    @discord.app_commands.command(name="reg", description="Создать список участников")
    async def reg(self, interaction: discord.Interaction, title: str, slots: int):
        if not has_reg_admin_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        if slots < 1 or slots > 500:
            await interaction.response.send_message("количество слотов должно быть от 1 до 500", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        mp_number = await get_next_mp_number()
        created_at_iso = discord.utils.utcnow().isoformat()

        data = {
            "title": title,
            "max_slots": slots,
            "main_list": [],
            "mp_number": mp_number,
            "creator_id": str(interaction.user.id),
            "created_at": created_at_iso,
            "closed": False,
        }

        embed = build_reg_embed(data)
        msg = await interaction.channel.send(embed=embed, view=RegView())
        thread = await msg.create_thread(name="плюсы")

        mp_log_message_id = None
        mp_log_thread_id = None
        mp_log_thread_link = None
        try:
            log_channel = interaction.guild.get_channel(MP_LOGS_CHANNEL_ID)
            if log_channel:
                log_embed = build_mp_log_embed(data, closed=False)
                log_msg = await log_channel.send(embed=log_embed)
                mp_log_message_id = str(log_msg.id)
                log_thread = await log_msg.create_thread(name=f"логи МП #{mp_number}")
                mp_log_thread_id = str(log_thread.id)
                mp_log_thread_link = log_thread.jump_url
        except Exception as e:
            print(f"[ERROR] mp log channel: {e}")

        data["mp_log_message_id"] = mp_log_message_id
        data["mp_log_thread_id"] = mp_log_thread_id
        data["mp_log_thread_link"] = mp_log_thread_link

        try:
            col = get_reg_collection()
            col.insert_one({
                "message_id": str(msg.id),
                "channel_id": str(interaction.channel.id),
                "thread_id": str(thread.id),
                **data
            })
        except Exception as e:
            print(f"[ERROR] MongoDB reg: {e}")

        await interaction.followup.send(f"мероприятие #{mp_number} создано", ephemeral=True)

    # ───────────────────────────────────────────────
    # Ивенты плюсов
    # ───────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not isinstance(message.channel, discord.Thread):
            return
        if message.author.bot:
            return
        if "+" not in message.content:
            return

        data = await get_reg_data_by_thread(message.channel.id)
        if not data or data.get("closed"):
            return

        try:
            col = get_reg_collection()
            msg_map = data.get("msg_map", {})
            msg_map[str(message.id)] = str(message.author.id)
            col.update_one({"_id": data["_id"]}, {"$set": {"msg_map": msg_map}})
        except Exception as e:
            print(f"[ERROR] on_message reg: {e}")

        await log_mp_event(self.bot, data, f"{message.author.display_name} откинул плюс")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != CHECKMARK_EMOJI:
            return
        if payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        reactor = guild.get_member(payload.user_id)
        if not reactor or not has_reg_admin_role(reactor):
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.Thread):
            return

        data = await get_reg_data_by_thread(channel.id)
        if not data or data.get("closed"):
            return

        try:
            msg = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        if msg.author.bot or "+" not in msg.content:
            return

        user_id = str(msg.author.id)
        main_list = data.get("main_list", [])
        max_slots = data["max_slots"]

        if any(u["id"] == user_id for u in main_list):
            return
        if len(main_list) >= max_slots:
            return

        member = guild.get_member(int(user_id))
        if not member:
            try:
                member = await guild.fetch_member(int(user_id))
            except Exception:
                return

        tier = get_tier_label(member)
        new_entry = {"id": user_id, "nick": member.display_name, "tier": tier}
        main_list = insert_by_tier(main_list, new_entry)

        data["main_list"] = main_list
        await save_reg_data(int(data["message_id"]), {"main_list": main_list})

        try:
            reg_channel = self.bot.get_channel(int(data["channel_id"]))
            reg_msg = await reg_channel.fetch_message(int(data["message_id"]))
            await update_reg_embed(reg_msg, data)
        except Exception as e:
            print(f"[ERROR] update embed on reaction add: {e}")

        await log_mp_event(self.bot, data, f"{reactor.display_name} вписал {member.display_name}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != CHECKMARK_EMOJI:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        reactor = guild.get_member(payload.user_id)
        if not reactor or not has_reg_admin_role(reactor):
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.Thread):
            return

        data = await get_reg_data_by_thread(channel.id)
        if not data or data.get("closed"):
            return

        try:
            msg = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        user_id = str(msg.author.id)
        main_list = data.get("main_list", [])
        new_list = [u for u in main_list if u["id"] != user_id]

        if len(new_list) == len(main_list):
            return

        data["main_list"] = new_list
        await save_reg_data(int(data["message_id"]), {"main_list": new_list})

        try:
            reg_channel = self.bot.get_channel(int(data["channel_id"]))
            reg_msg = await reg_channel.fetch_message(int(data["message_id"]))
            await update_reg_embed(reg_msg, data)
        except Exception as e:
            print(f"[ERROR] update embed on reaction remove: {e}")

        await log_mp_event(self.bot, data, f"{reactor.display_name} выписал {msg.author.display_name}")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.Thread):
            return

        data = await get_reg_data_by_thread(channel.id)
        if not data or data.get("closed"):
            return

        msg_map = data.get("msg_map", {})
        user_id = msg_map.get(str(payload.message_id))
        if not user_id:
            return

        main_list = data.get("main_list", [])
        was_in_list = any(u["id"] == user_id for u in main_list)
        new_list = [u for u in main_list if u["id"] != user_id]

        msg_map.pop(str(payload.message_id), None)
        data["main_list"] = new_list

        await save_reg_data(int(data["message_id"]), {"main_list": new_list, "msg_map": msg_map})

        try:
            reg_channel = self.bot.get_channel(int(data["channel_id"]))
            reg_msg = await reg_channel.fetch_message(int(data["message_id"]))
            await update_reg_embed(reg_msg, data)
        except Exception as e:
            print(f"[ERROR] update embed on message delete: {e}")

        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        nick = user_id
        if guild:
            member = guild.get_member(int(user_id))
            if member:
                nick = member.display_name

        if was_in_list:
            await log_mp_event(self.bot, data, f"{nick} убрал плюс и был выписан из списка")
        else:
            await log_mp_event(self.bot, data, f"{nick} убрал плюс")

    # ───────────────────────────────────────────────
    # Автоудаление старых МП
    # ───────────────────────────────────────────────

    async def mp_cleanup_loop(self):
        import asyncio
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                col = get_reg_collection()
                now = discord.utils.utcnow()
                cutoff = now - timedelta(days=MP_RETENTION_DAYS)
                closed_regs = list(col.find({"closed": True}))
                for reg in closed_regs:
                    closed_at = reg.get("closed_at")
                    if not closed_at:
                        continue
                    try:
                        closed_dt = discord.utils.parse_time(closed_at)
                    except Exception:
                        continue
                    if closed_dt < cutoff:
                        col.delete_one({"_id": reg["_id"]})
                        print(f"[INFO] Удалено старое МП #{reg.get('mp_number')}")
            except Exception as e:
                print(f"[ERROR] mp_cleanup_loop: {e}")
            await asyncio.sleep(3600)


async def setup(bot: commands.Bot):
    await bot.add_cog(RegCog(bot))
