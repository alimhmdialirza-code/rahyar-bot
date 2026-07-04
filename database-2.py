"""
لایه ذخیره‌سازی ربات رهیار قانون
همه اطلاعات کاربران و سفارش‌ها اینجا در یک فایل SQLite ذخیره می‌شه
تا با ری‌استارت شدن ربات، هیچ اطلاعاتی از دست نره.
"""

import sqlite3
import os
import logging
from contextlib import contextmanager

logger = logging.getLogger("rahyar_bot.database")

# روی Railway حتماً یک Volume به سرویس وصل کن (مثلاً روی مسیر /data)
# و متغیر DB_PATH رو به همون مسیر اشاره بده، وگرنه دیتابیس با هر دیپلوی پاک می‌شه.
DB_PATH = os.getenv("DB_PATH", "rahyar_bot.db")


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """ساخت جدول‌ها در صورت نبودن. یک‌بار در شروع برنامه صدا زده می‌شه."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id TEXT PRIMARY KEY,
                step TEXT DEFAULT 'get_name',
                name TEXT,
                family TEXT,
                phone TEXT,
                national_id TEXT,
                question_type TEXT,
                question_price TEXT,
                document_type TEXT,
                document_price TEXT,
                case_type TEXT,
                current_order_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                order_type TEXT NOT NULL,
                description TEXT,
                price TEXT,
                status TEXT DEFAULT 'pending_review',
                receipt_file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER,
                action TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    logger.info("پایگاه داده آماده است (%s)", DB_PATH)


# ---------- کاربران ----------

def get_user(chat_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        return dict(row) if row else None


def user_exists(chat_id):
    return get_user(chat_id) is not None


def create_user(chat_id, step="get_name"):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (chat_id, step) VALUES (?, ?)",
            (chat_id, step),
        )


def update_user(chat_id, **fields):
    """آپدیت هر تعداد فیلد دلخواه، مثلاً update_user(chat_id, step='main_menu', name='علی')"""
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [chat_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE users SET {columns} WHERE chat_id = ?", values)


def reset_user(chat_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
    create_user(chat_id, step="get_name")


# ---------- سفارش‌ها ----------

def create_order(chat_id, order_type, description="", price=""):
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO orders (chat_id, order_type, description, price) VALUES (?, ?, ?, ?)",
            (chat_id, order_type, description, price),
        )
        return cursor.lastrowid


def attach_receipt(order_id, file_id):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET receipt_file_id = ? WHERE id = ?", (file_id, order_id))


def set_order_status(order_id, status):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))


def get_order(order_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None


def get_pending_orders(limit=10):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE status = 'pending_review' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_orders_by_chat(chat_id, limit=20):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


# ---------- جستجو (برای ادمین) ----------

def search_users(query, limit=10):
    """جستجوی کاربر بر اساس نام، نام خانوادگی یا شماره تلفن."""
    like = f"%{query}%"
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM users
            WHERE name LIKE ? OR family LIKE ? OR phone LIKE ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (like, like, like, limit),
        ).fetchall()
        return [dict(row) for row in rows]


# ---------- لاگ عملکرد ادمین ----------

def log_admin_action(order_id, action):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO admin_actions (order_id, action) VALUES (?, ?)",
            (order_id, action),
        )


# ---------- آمار ----------

def get_stats():
    with get_connection() as conn:
        today_orders = conn.execute(
            "SELECT COUNT(*) c FROM orders WHERE date(created_at) = date('now')"
        ).fetchone()["c"]
        week_orders = conn.execute(
            "SELECT COUNT(*) c FROM orders WHERE created_at >= datetime('now', '-7 days')"
        ).fetchone()["c"]
        total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        today_users = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE date(created_at) = date('now')"
        ).fetchone()["c"]
        confirmed_orders = conn.execute(
            "SELECT COUNT(*) c FROM orders WHERE status = 'confirmed'"
        ).fetchone()["c"]
        pending_orders = conn.execute(
            "SELECT COUNT(*) c FROM orders WHERE status = 'pending_review'"
        ).fetchone()["c"]
        return {
            "today_orders": today_orders,
            "week_orders": week_orders,
            "total_users": total_users,
            "today_users": today_users,
            "confirmed_orders": confirmed_orders,
            "pending_orders": pending_orders,
        }
