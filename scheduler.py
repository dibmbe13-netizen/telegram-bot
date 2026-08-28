from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import (
    get_due_messages,
    mark_message_as_sent,
    mark_message_as_failed,
    get_message_destinations,
)


scheduler = AsyncIOScheduler()


async def send_to_destination(application, destination, message):
    content_type = message["content_type"]
    content = message["content"]
    file_name = message["file_name"]
    caption = message["caption"]
    telegram_chat_id = destination["telegram_chat_id"]

    if content_type == "text":
        await application.bot.send_message(
            chat_id=telegram_chat_id,
            text=content,
        )

    elif content_type == "document":
        await application.bot.send_document(
            chat_id=telegram_chat_id,
            document=content,
            filename=file_name,
            caption=caption,
        )

    elif content_type == "photo":
        await application.bot.send_photo(
            chat_id=telegram_chat_id,
            photo=content,
            caption=caption,
        )

    else:
        raise ValueError(
            f"Unsupported content type: {content_type}"
        )


async def check_scheduled_messages(application):
    due_messages = get_due_messages()

    for message in due_messages:
        message_id = message["id"]

        destinations = get_message_destinations(message_id)

        if not destinations:
            original_chat_id = message["chat_id"]

            if original_chat_id is None:
                error_message = "No destination configured."
                mark_message_as_failed(message_id, error_message)
                print(
                    f"Message {message_id} failed: "
                    f"{error_message}"
                )
                continue

            destinations = [{
                "telegram_chat_id": original_chat_id,
                "name": "Original Chat",
            }]

        errors = []

        for destination in destinations:
            try:
                await send_to_destination(
                    application,
                    destination,
                    message,
                )

                print(
                    f"Message {message_id} sent to "
                    f"{destination['name']}."
                )

            except Exception as error:
                error_text = str(error)

                errors.append(
                    f"{destination['name']}: {error_text}"
                )

                print(
                    f"Failed to send message {message_id} "
                    f"to {destination['name']}: "
                    f"{error_text}"
                )

        if errors:
            mark_message_as_failed(
                message_id,
                " | ".join(errors),
            )

        else:
            mark_message_as_sent(message_id)
            print(f"Message {message_id} completed.")


def start_scheduler(application):
    if scheduler.running:
        return

    scheduler.add_job(
        check_scheduled_messages,
        "interval",
        seconds=10,
        args=[application],
        id="check_scheduled_messages",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    print("Scheduler started.")
