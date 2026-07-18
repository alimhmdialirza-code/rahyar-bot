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
        if "campaign_id" not in discount_cols:
            conn.execute("ALTER TABLE discount_codes ADD COLUMN campaign_id INTEGER")
        if "assigned_to" not in discount_cols:
            conn.execute("ALTER TABLE discount_codes ADD COLUMN assigned_to TEXT")

        # ---------- سیستم کمپین (چارچوب عمومی برای هر نوع کمپین آینده) ----------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                percent_off INTEGER NOT NULL,
                max_uses_per_code INTEGER NOT NULL,
                pool_size INTEGER NOT NULL,
                auto_refill INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # این جدول عمداً هیچ‌وقت توسط delete_user_completely یا reset_user پاک نمی‌شه
        # چون تنها مرجع جلوگیری از دریافت دوباره‌ی هدیه با ساختن اکانت جدید (پاک کردن پروفایل) است.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS campaign_redemptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                campaign_id INTEGER NOT NULL,
                code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, campaign_id)
            )
        """)

        # مهاجرت ساده: اگه ستون‌های جدید روی جدول orders قدیمی نباشن، اضافه‌شون کن
        # (این کار داده‌های موجود رو پاک نمی‌کنه، فقط ستون جدید اضافه می‌کنه)
        existing_cols = [row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()]
        if "rating" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN rating INTEGER")
        if "reminded" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN reminded INTEGER DEFAULT 0")
        if "final_reply_text" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN final_reply_text TEXT")
        if "final_reply_type" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN final_reply_type TEXT")
        if "final_reply_file_id" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN final_reply_file_id TEXT")

        # مهاجرت users: فیلد موقت برای نگه‌داشتن متن قبل از تایید نهایی («مرور نهایی»)
        user_cols = [row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "pending_text" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN pending_text TEXT")

        # مهاجرت orders: فوریت، مبلغ تخفیف داده‌شده، موضوع، بازپرداخت، مصرف سوال تکمیلی
        if "urgent" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN urgent INTEGER DEFAULT 0")
        if "discount_amount" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN discount_amount INTEGER DEFAULT 0")
        if "topic" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN topic TEXT")
        if "refunded" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN refunded INTEGER DEFAULT 0")
        if "followup_used" not in existing_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN followup_used INTEGER DEFAULT 0")

        # ---------- یادآوری‌های مهلت قانونی ----------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                title TEXT NOT NULL,
                due_date TEXT NOT NULL,
                notified INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ---------- نوبت‌دهی مشاوره تلفنی ----------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS consultation_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_label TEXT NOT NULL,
                price TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                booked_by_chat_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ---------- تیکت‌های فنی (جدا از سفارش‌های حقوقی) ----------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ---------- مهلت‌های قانونی از پیش تعریف‌شده (برای محاسبه‌گر) ----------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS legal_deadlines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                days INTEGER NOT NULL,
                legal_ref TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # دیتاست شروع مواعد قانونی؛ فقط اگه جدول خالی باشه پر می‌شه (اجرای مجدد init_db چیزی رو تکراری نمی‌کنه)
        seeded = conn.execute("SELECT COUNT(*) c FROM legal_deadlines").fetchone()["c"]
        if not seeded:
            starter_deadlines = [
                ("تجدیدنظرخواهی از حکم دادگاه بدوی (مدنی)", 20, "ماده ۳۳۶ ق.آ.د.م"),
                ("واخواهی نسبت به حکم غیابی (مدنی)", 20, "ماده ۳۰۶ ق.آ.د.م"),
                ("فرجام‌خواهی (مدنی)", 20, "ماده ۳۸۶ ق.آ.د.م"),
                ("اعتراض ثالث اجرایی", 20, "ماده ۱۴۶ قانون اجرای احکام مدنی"),
                ("تجدیدنظرخواهی از حکم دادگاه کیفری", 20, "ماده ۴۲۶ ق.آ.د.ک ۱۳۹۲"),
                ("واخواهی نسبت به حکم غیابی (کیفری)", 20, "ماده ۴۰۶ ق.آ.د.ک ۱۳۹۲"),
                ("فرجام‌خواهی (کیفری)", 20, "ماده ۴۲۸ ق.آ.د.ک ۱۳۹۲"),
                ("اعتراض به قرار منع/موقوفی تعقیب", 10, "ق.آ.د.ک ۱۳۹۲"),
            ]
            conn.executemany(
                "INSERT INTO legal_deadlines (title, days, legal_ref) VALUES (?, ?, ?)",
                starter_deadlines,
            )
    logger.info("پایگاه داده آماده است (%s)", DB_PATH)


# ---------- کاربران ----------

def get_user(chat_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        return dict(row) if row else None


def user_exists(chat_id):
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        return row is not None


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

def create_order(chat_id, order_type, description="", price="", urgent=False, topic=None):
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO orders (chat_id, order_type, description, price, urgent, topic) VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, order_type, description, price, 1 if urgent else 0, topic),
        )
        return cursor.lastrowid


def set_order_topic(order_id, topic):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET topic = ? WHERE id = ?", (topic, order_id))


def set_order_discount_amount(order_id, amount):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET discount_amount = ? WHERE id = ?", (amount, order_id))


def set_order_refunded(order_id):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET refunded = 1 WHERE id = ?", (order_id,))


def mark_followup_used(order_id):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET followup_used = 1 WHERE id = ?", (order_id,))


def attach_receipt(order_id, file_id):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET receipt_file_id = ? WHERE id = ?", (file_id, order_id))


def save_order_final_reply(order_id, text, attachment_type, file_id):
    """آخرین پاسخ/سند ارسالی به مشتری رو آرشیو می‌کنه تا بعداً از «وضعیت سفارش‌های من» قابل مشاهده باشه."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE orders SET final_reply_text = ?, final_reply_type = ?, final_reply_file_id = ? WHERE id = ?",
            (text, attachment_type, file_id, order_id),
        )


def set_order_status(order_id, status):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))


def get_order(order_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None


def get_order_completed_at(order_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT created_at FROM admin_actions WHERE order_id = ? AND action = 'completed' "
            "ORDER BY created_at DESC LIMIT 1",
            (order_id,),
        ).fetchone()
        return row["created_at"] if row else None


def get_pending_orders(limit=10):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE status = 'pending_review' ORDER BY urgent DESC, created_at DESC LIMIT ?",
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


def get_orders_between(start_date_str, end_date_str):
    """سفارش‌های بین دو تاریخ (شامل start، غیرشامل end)، برای گزارش ماهانه."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE date(created_at) >= ? AND date(created_at) < ?",
            (start_date_str, end_date_str),
        ).fetchall()
        return [dict(row) for row in rows]


def get_new_users_count_between(start_date_str, end_date_str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM users WHERE date(created_at) >= ? AND date(created_at) < ?",
            (start_date_str, end_date_str),
        ).fetchone()["c"]


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


def list_active_discount_codes(limit=30, campaign_id=None):
    with get_connection() as conn:
        if campaign_id is not None:
            rows = conn.execute(
                "SELECT * FROM discount_codes WHERE active = 1 AND campaign_id = ? ORDER BY created_at DESC LIMIT ?",
                (campaign_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM discount_codes WHERE active = 1 ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def deactivate_discount_code(code):
    with get_connection() as conn:
        conn.execute("UPDATE discount_codes SET active = 0 WHERE code = ?", (code,))


# ---------- کمپین‌ها (چارچوب عمومی) ----------

def create_campaign(name, trigger_type, percent_off, max_uses_per_code, pool_size, auto_refill=True):
    """یک کمپین جدید می‌سازه و بلافاصله اولین دسته کد رو براش تولید می‌کنه. آیدی کمپین رو برمی‌گردونه."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO campaigns (name, trigger_type, percent_off, max_uses_per_code, pool_size, auto_refill, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            (name, trigger_type, percent_off, max_uses_per_code, pool_size, 1 if auto_refill else 0),
        )
        campaign_id = cursor.lastrowid
    generate_codes_for_campaign(campaign_id)
    return campaign_id


def generate_codes_for_campaign(campaign_id):
    """بر اساس تنظیمات خود کمپین، pool_size کد جدید براش می‌سازه (برای پر کردن اولیه یا تکمیل خودکار)."""
    campaign = get_campaign(campaign_id)
    if not campaign:
        return []
    generated = []
    with get_connection() as conn:
        while len(generated) < campaign["pool_size"]:
            code = _generate_random_code()
            exists = conn.execute("SELECT 1 FROM discount_codes WHERE code = ?", (code,)).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO discount_codes
                    (code, name, description, percent_off, max_uses, used_count, active, campaign_id)
                VALUES (?, ?, ?, ?, ?, 0, 1, ?)
                """,
                (code, campaign["name"], "", campaign["percent_off"], campaign["max_uses_per_code"], campaign_id),
            )
            generated.append(code)
    return generated


def get_campaign(campaign_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return dict(row) if row else None


def get_campaigns(limit=50):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM campaigns ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_active_campaigns_by_trigger(trigger_type):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM campaigns WHERE trigger_type = ? AND status = 'active' ORDER BY created_at ASC",
            (trigger_type,),
        ).fetchall()
        return [dict(row) for row in rows]


def set_campaign_status(campaign_id, status):
    with get_connection() as conn:
        conn.execute("UPDATE campaigns SET status = ? WHERE id = ?", (status, campaign_id))


def get_campaign_code_stats(campaign_id):
    with get_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) c FROM discount_codes WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()["c"]
        unassigned = conn.execute(
            "SELECT COUNT(*) c FROM discount_codes WHERE campaign_id = ? AND assigned_to IS NULL AND active = 1",
            (campaign_id,),
        ).fetchone()["c"]
        used = conn.execute(
            "SELECT COUNT(*) c FROM discount_codes WHERE campaign_id = ? AND used_count > 0", (campaign_id,)
        ).fetchone()["c"]
        return {"total": total, "unassigned": unassigned, "used": used}


def get_unassigned_campaign_code(campaign_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM discount_codes WHERE campaign_id = ? AND assigned_to IS NULL AND active = 1 LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return dict(row) if row else None


def assign_discount_code(code, chat_id):
    with get_connection() as conn:
        conn.execute("UPDATE discount_codes SET assigned_to = ? WHERE code = ?", (chat_id, code))


def has_redeemed_campaign(chat_id, campaign_id):
    """این تابع همیشه از جدول دائمی campaign_redemptions می‌خونه که هیچ‌وقت پاک نمی‌شه،
    پس حتی اگه کاربر با /deletemydata پروفایلش رو پاک کنه، دوباره واجد شرایط دریافت هدیه نمی‌شه."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM campaign_redemptions WHERE chat_id = ? AND campaign_id = ?",
            (chat_id, campaign_id),
        ).fetchone()
        return row is not None


def record_campaign_redemption(chat_id, campaign_id, code):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO campaign_redemptions (chat_id, campaign_id, code) VALUES (?, ?, ?)",
            (chat_id, campaign_id, code),
        )


def assign_campaign_code_to_user(chat_id, campaign_id):
    """یک کد آزاد از کمپین به کاربر اختصاص می‌ده؛ اگه تموم شده بود و auto_refill روشن بود، دسته جدید می‌سازه.
    اگه با موفقیت کدی اختصاص داده بشه، همون رشته کد رو برمی‌گردونه، وگرنه None."""
    campaign = get_campaign(campaign_id)
    if not campaign or campaign["status"] != "active":
        return None
    if has_redeemed_campaign(chat_id, campaign_id):
        return None

    code_row = get_unassigned_campaign_code(campaign_id)
    if not code_row and campaign["auto_refill"]:
        generate_codes_for_campaign(campaign_id)
        code_row = get_unassigned_campaign_code(campaign_id)

    if not code_row:
        return None

    assign_discount_code(code_row["code"], chat_id)
    record_campaign_redemption(chat_id, campaign_id, code_row["code"])
    return code_row["code"]


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


def get_orders_for_date(date_str):
    """سفارش‌های ثبت‌شده در یک تاریخ مشخص (فرمت 'YYYY-MM-DD')، برای گزارش روزانه."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE date(created_at) = ?", (date_str,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_new_users_count_for_date(date_str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM users WHERE date(created_at) = ?", (date_str,)
        ).fetchone()["c"]


def get_all_user_chat_ids():
    """لیست آیدی چت همه کاربران ثبت‌نام‌کرده، برای ارسال پیام همگانی."""
    with get_connection() as conn:
        rows = conn.execute("SELECT chat_id FROM users").fetchall()
        return [row["chat_id"] for row in rows]


# ---------- قیمت (کمکی، برای گزارش‌ها) ----------

def parse_price_safe(price_str):
    """رشته قیمت (مثلاً '150,000' یا '150000 تومان') رو با خیال راحت به عدد صحیح تبدیل می‌کنه.
    اگه چیزی قابل‌تبدیل نبود، صفر برمی‌گردونه (هیچ‌وقت گزارش رو با استثنا خراب نمی‌کنه)."""
    if not price_str:
        return 0
    digits = "".join(ch for ch in str(price_str) if ch.isdigit())
    return int(digits) if digits else 0


# ---------- تاریخچه کامل مشتری (برای نمای ادمین) ----------

def get_user_order_summary(chat_id):
    """خلاصه کامل تعاملات یک مشتری: پروفایل + همه سفارش‌ها + مجموع پرداختی، برای نمای ادمین."""
    user = get_user(chat_id)
    orders = get_orders_by_chat(chat_id, limit=100)
    total_paid = sum(
        parse_price_safe(o["price"]) for o in orders if o["status"] in ("confirmed", "in_progress", "completed", "awaiting_customer_info")
    )
    return {
        "user": user,
        "orders": orders,
        "order_count": len(orders),
        "total_paid": total_paid,
    }


# ---------- یادآوری‌های مهلت قانونی ----------

def add_reminder(chat_id, title, due_date):
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO reminders (chat_id, title, due_date) VALUES (?, ?, ?)",
            (chat_id, title, due_date),
        )
        return cursor.lastrowid


def get_reminders_by_chat(chat_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE chat_id = ? ORDER BY due_date ASC", (chat_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def delete_reminder(reminder_id, chat_id):
    """فقط یادآوری متعلق به همون chat_id رو پاک می‌کنه (جلوگیری از حذف یادآوری بقیه)."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM reminders WHERE id = ? AND chat_id = ?", (reminder_id, chat_id)
        )


def get_due_reminders():
    """یادآوری‌هایی که تاریخ سررسیدشون رسیده یا گذشته و هنوز اطلاع‌رسانی نشدن."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE notified = 0 AND date(due_date) <= date('now')"
        ).fetchall()
        return [dict(row) for row in rows]


def mark_reminder_notified(reminder_id):
    with get_connection() as conn:
        conn.execute("UPDATE reminders SET notified = 1 WHERE id = ?", (reminder_id,))


# ---------- نوبت‌دهی مشاوره تلفنی ----------

def add_consultation_slot(slot_label, price):
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO consultation_slots (slot_label, price, status) VALUES (?, ?, 'open')",
            (slot_label, price),
        )
        return cursor.lastrowid


def get_open_consultation_slots():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM consultation_slots WHERE status = 'open' ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_consultation_slot(slot_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM consultation_slots WHERE id = ?", (slot_id,)).fetchone()
        return dict(row) if row else None


def book_consultation_slot(slot_id, chat_id):
    """فقط اگه هنوز 'open' باشه رزرو می‌کنه (جلوگیری از دو رزرو همزمان روی یک نوبت)."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE consultation_slots SET status = 'booked', booked_by_chat_id = ? "
            "WHERE id = ? AND status = 'open'",
            (chat_id, slot_id),
        )
        return cursor.rowcount > 0


def get_booked_slots_by_chat(chat_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM consultation_slots WHERE booked_by_chat_id = ? ORDER BY created_at ASC",
            (chat_id,),
        ).fetchall()
        return [dict(row) for row in rows]


# ---------- تیکت‌های فنی ----------

def create_ticket(chat_id, message):
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO tickets (chat_id, message, status) VALUES (?, ?, 'open')",
            (chat_id, message),
        )
        return cursor.lastrowid


def get_open_tickets():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE status = 'open' ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def close_ticket(ticket_id):
    with get_connection() as conn:
        conn.execute("UPDATE tickets SET status = 'closed' WHERE id = ?", (ticket_id,))


# ---------- مواعد قانونی از پیش تعریف‌شده (محاسبه‌گر) ----------

def get_legal_deadlines():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM legal_deadlines ORDER BY id ASC").fetchall()
        return [dict(row) for row in rows]


def get_legal_deadline(deadline_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM legal_deadlines WHERE id = ?", (deadline_id,)).fetchone()
        return dict(row) if row else None


def add_legal_deadline(title, days, legal_ref=""):
    """امکان افزودن مورد جدید به دیتاست مواعد، بدون نیاز به تغییر کد (خود کاربر هم از پنل ادمین می‌تونه اضافه کنه)."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO legal_deadlines (title, days, legal_ref) VALUES (?, ?, ?)",
            (title, days, legal_ref),
        )
        return cursor.lastrowid
