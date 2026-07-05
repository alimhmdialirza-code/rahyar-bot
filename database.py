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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS discount_codes (
                code TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                percent_off INTEGER NOT NULL,
                max_uses INTEGER NOT NULL,
                used_count INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # مهاجرت ساده برای دیتابیس‌های قدیمی‌تر که این ستون‌ها رو ندارن
        discount_cols = [row["name"] for row in conn.execute("PRAGMA table_info(discount_codes)").fetchall()]
        if "description" not in discount_cols:
            conn.execute("ALTER TABLE discount_codes ADD COLUMN description TEXT")
        if "active" not in discount_cols:
            conn.execute("ALTER TABLE discount_codes ADD COLUMN active INTEGER DEFAULT 1")
        if "name" not in discount_cols:
            conn.execute("ALTER TABLE discount_codes ADD COLUMN name TEXT")

        # مهاجرت ساده: اگه ستون‌های جدید روی جدول orders قدیمی نباشن، اضافه‌شون کن
        # (این کار داده‌های موجود رو پاک نمی‌کنه، فقط ستون جدید اضافه می‌کنه)
        existing_cols = [row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()]
        if "rating" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN rating INTEGER")
        if "reminded" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN reminded INTEGER DEFAULT 0")
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


def delete_user_completely(chat_id):
    """حذف کامل کاربر و تمام سفارش‌هاش (برای درخواست حذف داده طبق سیاست حریم خصوصی)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM orders WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))


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


def set_order_rating(order_id, rating):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET rating = ? WHERE id = ?", (rating, order_id))


def update_order_price(order_id, price):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET price = ? WHERE id = ?", (price, order_id))


def get_stalled_orders(hours=6):
    """سفارش‌هایی که بیش از X ساعته در انتظار بررسی موندن و هنوز یادآوری نشدن."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM orders
            WHERE status = 'pending_review' AND (reminded IS NULL OR reminded = 0)
              AND created_at <= datetime('now', ?)
            """,
            (f"-{hours} hours",),
        ).fetchall()
        return [dict(row) for row in rows]


def mark_order_reminded(order_id):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET reminded = 1 WHERE id = ?", (order_id,))


# ---------- کد تخفیف ----------

def _generate_random_code(length=6):
    import random
    import string
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def create_discount_code(code, percent_off, max_uses, description=""):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO discount_codes (code, description, percent_off, max_uses, used_count, active) "
            "VALUES (?, ?, ?, ?, COALESCE((SELECT used_count FROM discount_codes WHERE code = ?), 0), 1)",
            (code, description, percent_off, max_uses, code),
        )


def create_discount_codes_batch(name, description, percent_off, max_uses, quantity):
    """quantity تا کد تصادفی و یکتا با همون نام/توضیح/درصد/تعداد مجاز می‌سازه و لیست کدها رو برمی‌گردونه."""
    generated = []
    with get_connection() as conn:
        while len(generated) < quantity:
            code = _generate_random_code()
            exists = conn.execute("SELECT 1 FROM discount_codes WHERE code = ?", (code,)).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO discount_codes (code, name, description, percent_off, max_uses, used_count, active) "
                "VALUES (?, ?, ?, ?, ?, 0, 1)",
                (code, name, description, percent_off, max_uses),
            )
            generated.append(code)
    return generated


def get_discount_code(code):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM discount_codes WHERE code = ?", (code,)).fetchone()
        return dict(row) if row else None


def increment_discount_usage(code):
    with get_connection() as conn:
        conn.execute("UPDATE discount_codes SET used_count = used_count + 1 WHERE code = ?", (code,))


def list_active_discount_codes(limit=30):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM discount_codes WHERE active = 1 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def deactivate_discount_code(code):
    with get_connection() as conn:
        conn.execute("UPDATE discount_codes SET active = 0 WHERE code = ?", (code,))


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
