#!/usr/bin/env python3
"""Manually trigger today's daily ranking that was skipped."""
import asyncio
import os
import sys
sys.path.insert(0, '/root/eh-reader-bot')
os.chdir('/root/eh-reader-bot')

from dotenv import load_dotenv
load_dotenv()

TOKEN = os.environ.get('EHBOT_TELEGRAM_TOKEN', '')
print(f"TOKEN loaded: {'yes' if TOKEN else 'NO'}")

from bot import send_daily_ranking_to_store
from telegram import Bot

async def main():
    bot = Bot(token=TOKEN)
    class FakeContext:
        def __init__(self):
            self.bot = bot
            self.job_queue = None
            self.job = None
            self.chat_data = {}
            self.user_data = {}
            self.bot_data = {}
            self.application = type('obj', (object,), {'bot': bot})()
    ctx = FakeContext()
    print("[手动触发] 开始执行每日排行...")
    await send_daily_ranking_to_store(ctx)
    print("[手动触发] 完成！")

asyncio.run(main())
