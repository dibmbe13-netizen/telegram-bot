import os
from datetime import datetime

from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from database import (
    create_tables,
    save_scheduled_message,
    get_pending_messages,
    get_failed_messages,
    retry_failed_message,
    delete_scheduled_message,
    add_destination,
    get_destinations,
    delete_destination,
    add_message_destination,
    get_message_destination_names,
    reschedule_scheduled_message,
)
from scheduler import start_scheduler


load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CONTENT, CAPTION, DATETIME, DESTINATIONS, RESCHEDULE = range(5)


# -----------------------------------
# BASIC COMMANDS
# -----------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello! 👋 I am your scheduled message bot.\n\n"
        "📅 Scheduling:\n"
        "/schedule - Create a scheduled message\n"
        "/tasks - View pending tasks\n"
        "/failed - View failed tasks\n"
        "/retry ID - Retry a failed task\n"
        "/delete ID - Delete a pending or failed task\n"
        "/cancel - Cancel the current action\n\n"
        "📍 Destinations:\n"
        "/chatid - Show the current chat ID\n"
        "/adddestination CHAT_ID NAME\n"
        "/destinations - View destinations\n"
        "/deletedestination ID - Remove a destination"
    )


async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    chat_title = chat.title or "Private Chat"

    await update.message.reply_text(
        f"📍 Chat ID: {chat.id}\n"
        f"Chat: {chat_title}"
    )


# -----------------------------------
# DESTINATION SELECTION
# -----------------------------------


def create_destination_keyboard(
    destinations,
    selected_destination_ids,
):
    keyboard = []

    for destination in destinations:
        destination_id = destination["id"]

        if destination_id in selected_destination_ids:
            button_text = f"☑ {destination['name']}"
        else:
            button_text = f"☐ {destination['name']}"

        keyboard.append([
            InlineKeyboardButton(
                button_text,
                callback_data=f"destination:{destination_id}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "✅ Confirm Selection",
            callback_data="confirm_destinations",
        )
    ])

    keyboard.append([
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data="cancel_destinations",
        )
    ])

    return InlineKeyboardMarkup(keyboard)


async def show_destination_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    owner_chat_id = update.effective_chat.id
    destinations = get_destinations(owner_chat_id)

    if not destinations:
        await update.message.reply_text(
            "You don't have any destinations yet. ❌\n\n"
            "First add one using:\n"
            "/adddestination CHAT_ID NAME"
        )
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data["available_destinations"] = [
        dict(destination)
        for destination in destinations
    ]
    context.user_data["selected_destination_ids"] = []

    await update.message.reply_text(
        "📍 Select one or more destinations.\n\n"
        "Tap a destination to select it, then press "
        "✅ Confirm Selection.",
        reply_markup=create_destination_keyboard(
            destinations,
            [],
        ),
    )

    return DESTINATIONS


async def handle_destination_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    callback_data = query.data

    destinations = context.user_data.get(
        "available_destinations",
        [],
    )
    selected_destination_ids = context.user_data.get(
        "selected_destination_ids",
        [],
    )

    if callback_data == "cancel_destinations":
        context.user_data.clear()
        await query.edit_message_text("❌ Scheduling cancelled.")
        return ConversationHandler.END

    if callback_data == "confirm_destinations":
        if not selected_destination_ids:
            await query.answer(
                "Please select at least one destination.",
                show_alert=True,
            )
            return DESTINATIONS

        return await save_final_scheduled_message(
            query,
            context,
        )

    if callback_data.startswith("destination:"):
        try:
            destination_id = int(
                callback_data.split(":", 1)[1]
            )
        except ValueError:
            await query.answer("Invalid destination.")
            return DESTINATIONS

        if destination_id in selected_destination_ids:
            selected_destination_ids.remove(destination_id)
        else:
            selected_destination_ids.append(destination_id)

        context.user_data[
            "selected_destination_ids"
        ] = selected_destination_ids

        await query.edit_message_reply_markup(
            reply_markup=create_destination_keyboard(
                destinations,
                selected_destination_ids,
            )
        )

    return DESTINATIONS


async def save_final_scheduled_message(
    query,
    context: ContextTypes.DEFAULT_TYPE,
):
    selected_ids = context.user_data.get(
        "selected_destination_ids",
        [],
    )
    destinations = context.user_data.get(
        "available_destinations",
        [],
    )

    message_id = save_scheduled_message(
        content_type=context.user_data["content_type"],
        content=context.user_data["content"],
        scheduled_time=context.user_data["scheduled_time"],
        chat_id=query.message.chat.id,
        file_name=context.user_data.get("file_name"),
        caption=context.user_data.get("caption"),
    )

    selected_names = []

    for destination in destinations:
        if destination["id"] in selected_ids:
            add_message_destination(
                message_id,
                destination["id"],
            )
            selected_names.append(destination["name"])

    scheduled_datetime = datetime.fromisoformat(
        context.user_data["scheduled_time"]
    )

    destinations_text = "\n".join(
        f"• {name}" for name in selected_names
    )

    await query.edit_message_text(
        f"✅ Task scheduled successfully!\n\n"
        f"Schedule ID: {message_id}\n"
        f"Type: {context.user_data['content_type']}\n"
        f"Time: "
        f"{scheduled_datetime.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"Destinations:\n{destinations_text}"
    )

    context.user_data.clear()
    return ConversationHandler.END


# -----------------------------------
# SCHEDULING
# -----------------------------------


async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "Send me the text message, photo, or document "
        "you want to schedule.\n\n"
        "Use /cancel to stop."
    )

    return CONTENT


async def get_content(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.message.text:
        context.user_data["content_type"] = "text"
        context.user_data["content"] = update.message.text
        context.user_data["file_name"] = None
        context.user_data["caption"] = None

        await ask_for_datetime(update)
        return DATETIME

    if update.message.document:
        context.user_data["content_type"] = "document"
        context.user_data["content"] = (
            update.message.document.file_id
        )
        context.user_data["file_name"] = (
            update.message.document.file_name
        )
        context.user_data["caption"] = None

        await update.message.reply_text(
            "📄 You sent a document.\n\n"
            "Send a caption for it, or use /skip."
        )
        return CAPTION

    if update.message.photo:
        context.user_data["content_type"] = "photo"
        context.user_data["content"] = (
            update.message.photo[-1].file_id
        )
        context.user_data["file_name"] = None
        context.user_data["caption"] = None

        await update.message.reply_text(
            "📸 You sent a photo.\n\n"
            "Send a caption for it, or use /skip."
        )
        return CAPTION

    return CONTENT


async def get_caption(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data["caption"] = update.message.text
    await ask_for_datetime(update)
    return DATETIME


async def skip_caption(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data["caption"] = None
    await ask_for_datetime(update)
    return DATETIME


async def ask_for_datetime(update: Update):
    await update.message.reply_text(
        "When should I send it?\n\n"
        "Use 24-hour format:\n"
        "YYYY-MM-DD HH:MM\n\n"
        "Example:\n"
        "2026-08-30 18:00"
    )


async def get_datetime(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    datetime_text = update.message.text

    try:
        scheduled_datetime = datetime.strptime(
            datetime_text,
            "%Y-%m-%d %H:%M",
        )
    except ValueError:
        await update.message.reply_text(
            "Invalid date or time format. ❌\n\n"
            "Please use:\n"
            "YYYY-MM-DD HH:MM"
        )
        return DATETIME

    if scheduled_datetime <= datetime.now():
        await update.message.reply_text(
            "❌ That time is in the past.\n\n"
            "Please enter a future date and time."
        )
        return DATETIME

    context.user_data["scheduled_time"] = (
        scheduled_datetime.isoformat()
    )

    return await show_destination_buttons(
        update,
        context,
    )


# -----------------------------------
# INTERACTIVE TASK BUTTONS
# -----------------------------------


def create_task_keyboard(message_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📄 View Details",
                callback_data=f"task_view:{message_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🕒 Reschedule",
                callback_data=f"task_reschedule:{message_id}",
            ),
            InlineKeyboardButton(
                "🗑 Delete",
                callback_data=f"task_delete:{message_id}",
            ),
        ],
    ])


async def show_task_details(query, task):
    scheduled_time = datetime.fromisoformat(
        task["scheduled_time"]
    )

    destination_names = get_message_destination_names(
        task["id"]
    )

    destinations_text = (
        ", ".join(destination_names)
        if destination_names
        else "No destination"
    )

    details = (
        f"📄 Task Details\n\n"
        f"ID: {task['id']}\n"
        f"Type: {task['content_type']}\n"
        f"Time: "
        f"{scheduled_time.strftime('%Y-%m-%d %H:%M')}\n"
        f"Status: {task['status']}\n"
        f"Destinations: {destinations_text}"
    )

    if task["content_type"] == "text":
        details += f"\n\nMessage:\n{task['content']}"
    elif task["content_type"] == "document":
        details += f"\n\nDocument: {task['file_name']}"
        if task["caption"]:
            details += f"\nCaption: {task['caption']}"
    elif task["content_type"] == "photo":
        details += "\n\nContent: Photo"
        if task["caption"]:
            details += f"\nCaption: {task['caption']}"

    await query.message.reply_text(details)


async def handle_task_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    try:
        action, message_id_text = query.data.split(":", 1)
        message_id = int(message_id_text)
    except (ValueError, AttributeError):
        await query.answer("Invalid task.")
        return ConversationHandler.END

    chat_id = query.message.chat.id
    task = next(
        (
            item
            for item in get_pending_messages(chat_id)
            if item["id"] == message_id
        ),
        None,
    )

    if task is None:
        await query.message.reply_text(
            "❌ This task no longer exists or is no longer pending."
        )
        return ConversationHandler.END

    if action == "task_view":
        await show_task_details(query, task)
        return ConversationHandler.END

    if action == "task_delete":
        deleted = delete_scheduled_message(
            message_id,
            chat_id,
        )

        if deleted:
            await query.edit_message_text(
                f"🗑 Task {message_id} deleted successfully."
            )
        else:
            await query.edit_message_text(
                "❌ Task could not be deleted."
            )

        return ConversationHandler.END

    if action == "task_reschedule":
        context.user_data["reschedule_message_id"] = (
            message_id
        )

        await query.message.reply_text(
            f"🕒 Rescheduling Task {message_id}.\n\n"
            "Send the new date and time:\n\n"
            "YYYY-MM-DD HH:MM\n\n"
            "Use 24-hour format."
        )

        return RESCHEDULE

    return ConversationHandler.END


async def get_new_schedule_time(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message_id = context.user_data.get(
        "reschedule_message_id"
    )

    if message_id is None:
        await update.message.reply_text(
            "❌ No task selected for rescheduling."
        )
        return ConversationHandler.END

    try:
        new_datetime = datetime.strptime(
            update.message.text,
            "%Y-%m-%d %H:%M",
        )
    except ValueError:
        await update.message.reply_text(
            "Invalid format. ❌\n\n"
            "Please use:\n"
            "YYYY-MM-DD HH:MM"
        )
        return RESCHEDULE

    if new_datetime <= datetime.now():
        await update.message.reply_text(
            "❌ That time is in the past."
        )
        return RESCHEDULE

    updated = reschedule_scheduled_message(
        message_id=message_id,
        chat_id=update.effective_chat.id,
        new_scheduled_time=new_datetime.isoformat(),
    )

    context.user_data.pop(
        "reschedule_message_id",
        None,
    )

    if updated:
        await update.message.reply_text(
            f"✅ Task {message_id} rescheduled successfully!\n\n"
            f"New time:\n"
            f"{new_datetime.strftime('%Y-%m-%d %H:%M')}"
        )
    else:
        await update.message.reply_text(
            "❌ Task could not be rescheduled."
        )

    return ConversationHandler.END


# -----------------------------------
# TASK MANAGEMENT
# -----------------------------------


async def show_tasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    tasks = get_pending_messages(
        update.effective_chat.id
    )

    if not tasks:
        await update.message.reply_text(
            "You don't have any pending tasks."
        )
        return

    await update.message.reply_text(
        f"📋 You have {len(tasks)} pending task(s).\n\n"
        "Use the buttons below each task."
    )

    for task in tasks:
        scheduled_time = datetime.fromisoformat(
            task["scheduled_time"]
        )

        destination_names = (
            get_message_destination_names(task["id"])
        )

        destinations_text = (
            ", ".join(destination_names)
            if destination_names
            else "None"
        )

        response = (
            f"📌 Task ID: {task['id']}\n"
            f"Type: {task['content_type']}\n"
            f"Time: "
            f"{scheduled_time.strftime('%Y-%m-%d %H:%M')}\n"
            f"Destinations: {destinations_text}"
        )

        await update.message.reply_text(
            response,
            reply_markup=create_task_keyboard(task["id"]),
        )


async def show_failed_tasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    tasks = get_failed_messages(
        update.effective_chat.id
    )

    if not tasks:
        await update.message.reply_text(
            "You don't have any failed tasks. 🎉"
        )
        return

    response = "❌ Failed tasks:\n\n"

    for task in tasks:
        scheduled_time = datetime.fromisoformat(
            task["scheduled_time"]
        )

        response += (
            f"ID: {task['id']}\n"
            f"Type: {task['content_type']}\n"
            f"Scheduled: "
            f"{scheduled_time.strftime('%Y-%m-%d %H:%M')}\n"
            f"Error: {task['last_error'] or 'Unknown error'}\n\n"
        )

    response += (
        "Retry one using:\n"
        "/retry ID\n\n"
        "Delete one using:\n"
        "/delete ID"
    )

    await update.message.reply_text(response)


async def retry_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.args:
        await update.message.reply_text(
            "Example:\n/retry 3"
        )
        return

    try:
        message_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "Task ID must be a number."
        )
        return

    retried = retry_failed_message(
        message_id,
        update.effective_chat.id,
    )

    if retried:
        await update.message.reply_text(
            f"🔄 Task {message_id} is pending again. "
            "The scheduler will retry it shortly."
        )
    else:
        await update.message.reply_text(
            "Failed task not found."
        )


async def delete_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.args:
        await update.message.reply_text(
            "Example:\n/delete 3"
        )
        return

    try:
        message_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "Task ID must be a number."
        )
        return

    deleted = delete_scheduled_message(
        message_id,
        update.effective_chat.id,
    )

    if deleted:
        await update.message.reply_text(
            f"🗑 Task {message_id} deleted."
        )
    else:
        await update.message.reply_text(
            "Task not found."
        )


# -----------------------------------
# DESTINATION MANAGEMENT
# -----------------------------------


async def add_new_destination(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    owner_chat_id = update.effective_chat.id

    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "/adddestination CHAT_ID NAME\n\n"
            "Example:\n"
            "/adddestination -1001234567890 My Channel"
        )
        return

    try:
        telegram_chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "CHAT_ID must be a number."
        )
        return

    name = " ".join(context.args[1:])

    destination_id = add_destination(
        owner_chat_id,
        telegram_chat_id,
        name,
    )

    if destination_id is None:
        await update.message.reply_text(
            "That destination is already registered."
        )
    else:
        await update.message.reply_text(
            f"✅ Destination added!\n\n"
            f"ID: {destination_id}\n"
            f"Name: {name}"
        )


async def show_destinations(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    destinations = get_destinations(
        update.effective_chat.id
    )

    if not destinations:
        await update.message.reply_text(
            "You don't have any destinations yet.\n\n"
            "Use:\n/adddestination CHAT_ID NAME"
        )
        return

    response = "📍 Your destinations:\n\n"

    for destination in destinations:
        response += (
            f"ID: {destination['id']}\n"
            f"Name: {destination['name']}\n"
            f"Chat ID: "
            f"{destination['telegram_chat_id']}\n\n"
        )

    response += (
        "Remove one using:\n"
        "/deletedestination ID"
    )

    await update.message.reply_text(response)


async def remove_destination(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.args:
        await update.message.reply_text(
            "Example:\n/deletedestination 2"
        )
        return

    try:
        destination_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "Destination ID must be a number."
        )
        return

    deleted = delete_destination(
        destination_id,
        update.effective_chat.id,
    )

    if deleted:
        await update.message.reply_text(
            f"🗑 Destination {destination_id} deleted."
        )
    else:
        await update.message.reply_text(
            "Destination not found."
        )


# -----------------------------------
# CANCEL
# -----------------------------------


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Current action cancelled."
    )

    return ConversationHandler.END


# -----------------------------------
# APPLICATION SETUP
# -----------------------------------


async def post_init(application: Application):
    start_scheduler(application)


def main():
    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing from your .env file."
        )

    create_tables()

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    scheduling_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("schedule", schedule)
        ],
        states={
            CONTENT: [
                MessageHandler(
                    (
                        filters.TEXT
                        | filters.PHOTO
                        | filters.Document.ALL
                    )
                    & ~filters.COMMAND,
                    get_content,
                )
            ],
            CAPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_caption,
                ),
                CommandHandler("skip", skip_caption),
            ],
            DATETIME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_datetime,
                )
            ],
            DESTINATIONS: [
                CallbackQueryHandler(
                    handle_destination_button,
                    pattern=(
                        r"^(destination:\d+|"
                        r"confirm_destinations|"
                        r"cancel_destinations)$"
                    ),
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel)
        ],
        per_message=False,
    )

    task_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                handle_task_button,
                pattern=(
                    r"^task_(view|delete|reschedule):\d+$"
                ),
            )
        ],
        states={
            RESCHEDULE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_new_schedule_time,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel)
        ],
        per_message=False,
    )

    app.add_handler(
        CommandHandler("start", start)
    )
    app.add_handler(
        CommandHandler("chatid", get_chat_id)
    )
    app.add_handler(
        CommandHandler("tasks", show_tasks)
    )
    app.add_handler(
        CommandHandler("failed", show_failed_tasks)
    )
    app.add_handler(
        CommandHandler("retry", retry_task)
    )
    app.add_handler(
        CommandHandler("delete", delete_task)
    )
    app.add_handler(
        CommandHandler(
            "adddestination",
            add_new_destination,
        )
    )
    app.add_handler(
        CommandHandler(
            "destinations",
            show_destinations,
        )
    )
    app.add_handler(
        CommandHandler(
            "deletedestination",
            remove_destination,
        )
    )

    app.add_handler(scheduling_conversation)
    app.add_handler(task_conversation)

    print("Bot is starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
