import os
import asyncio

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder

from database import create_tables
from scheduler import check_scheduled_messages


async def main():
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    create_tables()

    application = (
        ApplicationBuilder()
        .token(token)
        .build()
    )

    print("Checking for due scheduled messages...")

    await application.initialize()

    try:
        await check_scheduled_messages(application)
    finally:
        await application.shutdown()

    print("Daily check completed.")


if __name__ == "__main__":
    asyncio.run(main())