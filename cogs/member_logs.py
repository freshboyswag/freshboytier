import discord
from discord.ext import commands

from config import MEMBER_LOG_CHANNEL_ID
from database import get_db, is_log_enabled


class MemberLogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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


async def setup(bot: commands.Bot):
    await bot.add_cog(MemberLogsCog(bot))
