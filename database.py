import sqlite3
from datetime import datetime


DATABASE_NAME = "scheduler.db"


def get_connection():
    connection = sqlite3.connect(DATABASE_NAME)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_tables():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_type TEXT NOT NULL,
            content TEXT NOT NULL,
            file_name TEXT,
            caption TEXT,
            scheduled_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            chat_id INTEGER NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS destinations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_chat_id INTEGER NOT NULL,
            telegram_chat_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_chat_id, telegram_chat_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS message_destinations (
            message_id INTEGER NOT NULL,
            destination_id INTEGER NOT NULL,
            PRIMARY KEY (message_id, destination_id),
            FOREIGN KEY (message_id)
                REFERENCES scheduled_messages(id)
                ON DELETE CASCADE,
            FOREIGN KEY (destination_id)
                REFERENCES destinations(id)
                ON DELETE CASCADE
        )
    """)

    cursor.execute("PRAGMA table_info(scheduled_messages)")
    existing_columns = {
        column["name"] for column in cursor.fetchall()
    }

    if "caption" not in existing_columns:
        cursor.execute(
            "ALTER TABLE scheduled_messages ADD COLUMN caption TEXT"
        )

    if "last_error" not in existing_columns:
        cursor.execute(
            "ALTER TABLE scheduled_messages ADD COLUMN last_error TEXT"
        )

    connection.commit()
    connection.close()


# -----------------------------------
# SCHEDULED MESSAGE FUNCTIONS
# -----------------------------------


def save_scheduled_message(
    content_type,
    content,
    scheduled_time,
    chat_id,
    file_name=None,
    caption=None,
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO scheduled_messages (
            content_type,
            content,
            file_name,
            caption,
            scheduled_time,
            chat_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        content_type,
        content,
        file_name,
        caption,
        scheduled_time,
        chat_id,
    ))

    message_id = cursor.lastrowid
    connection.commit()
    connection.close()

    return message_id


def get_due_messages():
    connection = get_connection()
    cursor = connection.cursor()

    current_time = datetime.now().isoformat()

    cursor.execute("""
        SELECT *
        FROM scheduled_messages
        WHERE status = 'pending'
        AND scheduled_time <= ?
        ORDER BY scheduled_time ASC
    """, (current_time,))

    messages = cursor.fetchall()
    connection.close()

    return messages


def mark_message_as_sent(message_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        UPDATE scheduled_messages
        SET status = 'sent',
            last_error = NULL
        WHERE id = ?
    """, (message_id,))

    connection.commit()
    connection.close()


def mark_message_as_failed(message_id, error_message):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        UPDATE scheduled_messages
        SET status = 'failed',
            last_error = ?
        WHERE id = ?
    """, (error_message, message_id))

    connection.commit()
    connection.close()


def get_pending_messages(chat_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT *
        FROM scheduled_messages
        WHERE chat_id = ?
        AND status = 'pending'
        ORDER BY scheduled_time ASC
    """, (chat_id,))

    messages = cursor.fetchall()
    connection.close()

    return messages


def get_failed_messages(chat_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT *
        FROM scheduled_messages
        WHERE chat_id = ?
        AND status = 'failed'
        ORDER BY scheduled_time DESC
    """, (chat_id,))

    messages = cursor.fetchall()
    connection.close()

    return messages


def retry_failed_message(message_id, chat_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        UPDATE scheduled_messages
        SET status = 'pending',
            last_error = NULL
        WHERE id = ?
        AND chat_id = ?
        AND status = 'failed'
    """, (message_id, chat_id))

    retried = cursor.rowcount > 0
    connection.commit()
    connection.close()

    return retried


def delete_scheduled_message(message_id, chat_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id
        FROM scheduled_messages
        WHERE id = ?
        AND chat_id = ?
        AND status IN ('pending', 'failed')
    """, (message_id, chat_id))

    message = cursor.fetchone()

    if message is None:
        connection.close()
        return False

    cursor.execute("""
        DELETE FROM message_destinations
        WHERE message_id = ?
    """, (message_id,))

    cursor.execute("""
        DELETE FROM scheduled_messages
        WHERE id = ?
    """, (message_id,))

    connection.commit()
    connection.close()

    return True


def reschedule_scheduled_message(
    message_id,
    chat_id,
    new_scheduled_time,
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        UPDATE scheduled_messages
        SET scheduled_time = ?,
            last_error = NULL
        WHERE id = ?
        AND chat_id = ?
        AND status = 'pending'
    """, (
        new_scheduled_time,
        message_id,
        chat_id,
    ))

    updated = cursor.rowcount > 0
    connection.commit()
    connection.close()

    return updated


# -----------------------------------
# DESTINATION FUNCTIONS
# -----------------------------------


def add_destination(owner_chat_id, telegram_chat_id, name):
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            INSERT INTO destinations (
                owner_chat_id,
                telegram_chat_id,
                name
            )
            VALUES (?, ?, ?)
        """, (
            owner_chat_id,
            telegram_chat_id,
            name,
        ))

        destination_id = cursor.lastrowid
        connection.commit()
        return destination_id

    except sqlite3.IntegrityError:
        return None

    finally:
        connection.close()


def get_destinations(owner_chat_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT *
        FROM destinations
        WHERE owner_chat_id = ?
        ORDER BY id ASC
    """, (owner_chat_id,))

    destinations = cursor.fetchall()
    connection.close()

    return destinations


def delete_destination(destination_id, owner_chat_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id
        FROM destinations
        WHERE id = ?
        AND owner_chat_id = ?
    """, (destination_id, owner_chat_id))

    if cursor.fetchone() is None:
        connection.close()
        return False

    cursor.execute("""
        DELETE FROM message_destinations
        WHERE destination_id = ?
    """, (destination_id,))

    cursor.execute("""
        DELETE FROM destinations
        WHERE id = ?
        AND owner_chat_id = ?
    """, (destination_id, owner_chat_id))

    connection.commit()
    connection.close()

    return True


# -----------------------------------
# MESSAGE DESTINATION FUNCTIONS
# -----------------------------------


def add_message_destination(message_id, destination_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT OR IGNORE INTO message_destinations (
            message_id,
            destination_id
        )
        VALUES (?, ?)
    """, (message_id, destination_id))

    connection.commit()
    connection.close()


def get_message_destinations(message_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            destinations.id,
            destinations.telegram_chat_id,
            destinations.name
        FROM destinations
        JOIN message_destinations
            ON destinations.id =
               message_destinations.destination_id
        WHERE message_destinations.message_id = ?
        ORDER BY destinations.id ASC
    """, (message_id,))

    destinations = cursor.fetchall()
    connection.close()

    return destinations


def get_message_destination_names(message_id):
    return [
        destination["name"]
        for destination in get_message_destinations(message_id)
    ]
