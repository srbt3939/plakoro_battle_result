import asyncio
import logging
import os
from pathlib import Path

import discord

from battle_parser import parse_discord_message, register_battles


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("BATTLE_DB_PATH", str(BASE_DIR / "pokemon.db"))
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DEFAULT_PLAYER2_ID = os.getenv("DEFAULT_PLAYER2_ID")


def read_channel_ids() -> set[int]:
    value = os.getenv("DISCORD_CHANNEL_IDS", "")
    return {int(channel_id.strip()) for channel_id in value.split(",") if channel_id.strip()}


ALLOWED_CHANNEL_IDS = read_channel_ids()

intents = discord.Intents.default()
intents.message_content = True

bot = discord.Client(intents=intents)


def get_opponent_id(message: discord.Message) -> int | None:
    mentioned_users = [
        user.id
        for user in message.mentions
        if user.id != message.author.id and not user.bot
    ]

    if mentioned_users:
        return mentioned_users[0]

    if DEFAULT_PLAYER2_ID:
        return int(DEFAULT_PLAYER2_ID)

    return None


@bot.event
async def on_ready() -> None:
    logging.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    if ALLOWED_CHANNEL_IDS and message.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    battles = parse_discord_message(message.content)
    if not battles:
        return

    player2_id = get_opponent_id(message)
    if player2_id is None:
        await message.reply(
            "戦績を検出しましたが、対戦相手を特定できません。相手をメンションするか、DEFAULT_PLAYER2_ID を設定してください。",
            mention_author=False,
        )
        return

    registered_ids = await asyncio.to_thread(
        register_battles,
        DB_PATH,
        message.id,
        message.author.id,
        player2_id,
        message.content,
        message.created_at.isoformat(),
    )

    if registered_ids:
        await message.reply(
            f"戦績を {len(registered_ids)} 件登録しました。",
            mention_author=False,
        )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN が設定されていません。")

    logging.basicConfig(level=logging.INFO)
    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()