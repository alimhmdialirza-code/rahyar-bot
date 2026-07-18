# -*- coding: utf-8 -*-
"""
ربات رهیار قانون
نسخه بازنویسی‌شده با:
  - خواندن تنظیمات حساس (توکن، آیدی ادمین، شماره کارت) از فایل .env
  - ذخیره‌سازی دائمی اطلاعات کاربران و سفارش‌ها در SQLite (database.py)
  - لانگ‌پولینگ به‌جای پولینگ هر ۲ ثانیه
  - اعتبارسنجی شماره تلفن و کد ملی
  - مدیریت خطا در ارسال عکس رسید به ادمین
  - دستور /cancel برای بازگشت به منو بدون پاک شدن اطلاعات
  - دکمه تایید/رد پرداخت برای ادمین
  - لاگ‌گیری با ماژول logging
  - بازنویسی متن دکمه‌ها و پیام‌ها
"""

import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import logging

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# تنظیمات و لاگ
# ---------------------------------------------------------------------------

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
CARD_NUMBER = os.getenv("CARD_NUMBER", "0000000000000000")
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "09931012756")

if not TOKEN or not ADMIN_CHAT_ID:
    raise RuntimeError(
        "متغیرهای BOT_TOKEN و ADMIN_CHAT_ID تنظیم نشده‌اند. "
        "فایل .env را بر اساس .env.example بسازید."
    )

API_URL = f"https://tapi.bale.ai/bot{TOKEN}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("rahyar_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("rahyar_bot")

# import database بعد از load_dotenv() و تنظیم لاگ انجام می‌شه تا اگه دیتابیس به متغیرهای
# محیطی نیاز داشت (مثلاً DB_PATH)، مقدارشون از قبل از فایل .env خونده شده باشه.
import database as db  # noqa: E402

# ---------------------------------------------------------------------------
# ثوابت عمومی
# ---------------------------------------------------------------------------

IRAN_TZ = ZoneInfo("Asia/Tehran")
BOT_VERSION = "1.7.0"
STALLED_ORDER_HOURS = 6                 # ساعت‌ها تا یادآوری سفارش معطل‌مونده
STALLED_CHECK_INTERVAL_SECONDS = 1800   # هر ۳۰ دقیقه یک‌بار
BACKUP_INTERVAL_SECONDS = 7 * 24 * 3600  # هفتگی
REMINDER_CHECK_INTERVAL_SECONDS = 3600   # هر ساعت یک‌بار چک یادآوری‌های مهلت قانونی
DAILY_REPORT_HOUR = 9                   # ساعت ۹ صبح به وقت ایران


def now_iran():
    return datetime.now(IRAN_TZ)


def get_version_info_text():
    """نسخه‌ای که دستی نگه‌داری می‌شه رو با شناسه خودکار کامیت گیت (اگه روی Railway باشیم) ترکیب می‌کنه.
    شناسه کامیت هیچ‌وقت نیاز به آپدیت دستی نداره — Railway خودش موقع هر دیپلوی میذارتش."""
    commit_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA", "")
    lines = [f"ℹ️ نسخه ربات رهیار قانون: {BOT_VERSION}"]
    if commit_sha:
        lines.append(f"🔧 شناسه کامیت فعلی: {commit_sha[:7]}")
    else:
        lines.append("🔧 شناسه کامیت: در دسترس نیست (فقط روی Railway نمایش داده می‌شه)")
    return "\n".join(lines)

# وقتی ادمین روی دکمه «پاسخ به این مشتری» بزنه، شماره سفارش موردنظر اینجا موقتاً نگه داشته می‌شه
# تا پیام بعدی ادمین به‌جای پردازش عادی، مستقیم برای همون مشتری ارسال بشه.
ADMIN_PENDING_REPLY = {"order_id": None}

# مشابه بالا، ولی برای وقتی که ادمین از مشتری اطلاعات بیشتری می‌خواد (نه پاسخ نهایی)
ADMIN_PENDING_ASKINFO = {"order_id": None}

# وضعیت ویزاردهای چندمرحله‌ای ادمین (ساخت کد تخفیف، جستجو، پیام مستقیم و ...)
ADMIN_WIZARD = {"flow": None, "step": None, "data": {}}


def cancel_wizard_keyboard():
    return make_keyboard([[("❌ لغو", "dcw_cancel")]])


def reset_admin_wizard():
    ADMIN_WIZARD["flow"] = None
    ADMIN_WIZARD["step"] = None
    ADMIN_WIZARD["data"] = {}

# ---------------------------------------------------------------------------
# محدودیت نرخ پیام (جلوگیری از اسپم)
# ---------------------------------------------------------------------------

_RECENT_MESSAGE_TIMES = {}
RATE_LIMIT_MAX_PER_MINUTE = 20


def is_rate_limited(chat_id):
    """اگه یک چت بیش از حد مجاز توی یک دقیقه پیام بفرسته، True برمی‌گردونه."""
    now = time.time()
    timestamps = _RECENT_MESSAGE_TIMES.get(chat_id, [])
    timestamps = [t for t in timestamps if now - t < 60]
    timestamps.append(now)
    _RECENT_MESSAGE_TIMES[chat_id] = timestamps
    return len(timestamps) > RATE_LIMIT_MAX_PER_MINUTE


# ---------------------------------------------------------------------------
# توابع کمکی ارتباط با API
# ---------------------------------------------------------------------------

def send_message(chat_id, text, keyboard=None):
    url = f"{API_URL}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    try:
        response = requests.post(url, data=data, timeout=10)
        if not response.ok:
            logger.error("sendMessage ناموفق برای %s: %s", chat_id, response.text)
            return False
        return True
    except Exception as e:
        logger.exception("خطا در ارسال پیام به %s: %s", chat_id, e)
        return False


def forward_photo_to_admin(file_id, caption=""):
    """ارسال عکس به ادمین. در صورت موفقیت True برمی‌گردونه، وگرنه False."""
    url = f"{API_URL}/sendPhoto"
    data = {"chat_id": ADMIN_CHAT_ID, "photo": file_id, "caption": caption}
    try:
        response = requests.post(url, data=data, timeout=10)
        if not response.ok:
            logger.error("sendPhoto به ادمین ناموفق: %s", response.text)
            return False
        return True
    except Exception as e:
        logger.exception("خطا در ارسال عکس به ادمین: %s", e)
        return False


def admin_order_action_buttons(order_id):
    """ردیف دکمه‌های استاندارد پاسخ/درخواست اطلاعات بیشتر که زیر پیام‌های مربوط به یک سفارش نشون داده می‌شه."""
    return [
        [("📩 پاسخ به این مشتری", f"adm_reply_{order_id}")],
        [("❓ درخواست اطلاعات بیشتر", f"adm_askinfo_{order_id}")],
    ]


def forward_receipt_to_admin(kind, file_id, caption, order_id):
    """ارسال رسید پرداخت (عکس یا فایل) به ادمین همراه با دکمه تایید/رد/پاسخ."""
    keyboard = make_keyboard([
        [
            ("✅ تایید پرداخت", f"adm_confirm_{order_id}"),
            ("❌ رد پرداخت", f"adm_reject_{order_id}"),
        ],
        *admin_order_action_buttons(order_id),
    ])
    return send_attachment(ADMIN_CHAT_ID, kind, file_id, caption, keyboard)


# نگه‌داشته شده برای سازگاری با کدهای قدیمی؛ نسخه جدید forward_receipt_to_admin است.
def forward_photo_to_admin_with_actions(file_id, caption, order_id):
    return forward_receipt_to_admin("photo", file_id, caption, order_id)


def make_keyboard(buttons):
    inline_keyboard = []
    for row in buttons:
        keyboard_row = [{"text": t, "callback_data": d} for t, d in row]
        inline_keyboard.append(keyboard_row)
    return {"inline_keyboard": inline_keyboard}


def back_keyboard(target, cancel_order=False, extra_rows=None):
    """دکمه بازگشت با برچسب واضح بر اساس این‌که سفارش نیمه‌کاره لغو می‌شه یا نه.
    target: اسم مرحله‌ای که باید بهش برگرده
    cancel_order: اگه True باشه، سفارش نیمه‌کاره فعلی لغو می‌شه — با برچسب متفاوت نشون داده می‌شه.
    extra_rows: ردیف‌های دکمه اضافه که قبل از دکمه بازگشت نمایش داده بشن."""
    rows = list(extra_rows) if extra_rows else []
    label = "🚫 انصراف و بازگشت" if cancel_order else "⬅️ بازگشت"
    rows.append([(label, f"goback|{target}|{1 if cancel_order else 0}")])
    return make_keyboard(rows)


def back_to_main_keyboard():
    """دکمه ساده «🔙 بازگشت به منوی اصلی» برای انتهای صفحات اطلاعاتی."""
    return make_keyboard([[("🔙 بازگشت به منوی اصلی", "back_main")]])


def admin_persistent_keyboard():
    """یک دکمه ثابت پایین صفحه چت ادمین که همیشه دیده می‌شه (نه inline، بلکه کیبورد اصلی)."""
    return {"keyboard": [["🛠 پنل مدیریت"]], "resize_keyboard": True}


def customer_persistent_keyboard():
    """یک دکمه ثابت پایین صفحه چت مشتری برای بازگشت سریع به منوی اصلی، هر جا که باشه."""
    return {"keyboard": [["📋 منوی اصلی"]], "resize_keyboard": True}


def forward_to_admin(user, subject, detail="", order_id=None):
    order_num = f"#{order_id}" if order_id else "—"
    now_str = now_iran().strftime("%Y/%m/%d — %H:%M")
    text = (
        f"📨 سفارش جدید {order_num}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👤 نام: {user.get('name', '')} {user.get('family', '')}\n"
        f"📞 تماس: {user.get('phone', '')}\n"
        f"🪪 کد ملی: {user.get('national_id') or 'ثبت نشده'}\n"
        f"🆔 چت آیدی: {user.get('chat_id', '')}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📌 نوع: {subject}\n"
        f"{detail}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🕒 {now_str}"
    )
    keyboard = None
    if order_id:
        keyboard = make_keyboard(admin_order_action_buttons(order_id))
    return send_message(ADMIN_CHAT_ID, text, keyboard)


# ---------------------------------------------------------------------------
# ارسال/دریافت مدارک (عکس، فایل، ویس)
# ---------------------------------------------------------------------------

ATTACHMENT_LABELS = {"photo": "🖼 تصویر", "document": "📎 فایل", "voice": "🎙 پیام صوتی"}


def _extract_file_id(value):
    """چه value یک دیکشنری تکی باشه چه یک لیست از دیکشنری‌ها (اندازه‌های مختلف عکس)، file_id رو پیدا می‌کنه."""
    if isinstance(value, list) and value:
        last = value[-1]
        if isinstance(last, dict):
            return last.get("file_id")
        return None
    if isinstance(value, dict):
        return value.get("file_id")
    return None


def extract_incoming_attachment(message):
    """اگه پیام ورودی شامل عکس، فایل، یا ویس باشه، نوع و file_id اونو برمی‌گردونه."""
    if message.get("voice"):
        file_id = _extract_file_id(message["voice"])
        if file_id:
            return "voice", file_id
    if message.get("document"):
        file_id = _extract_file_id(message["document"])
        if file_id:
            return "document", file_id
    if message.get("photo"):
        file_id = _extract_file_id(message["photo"])
        if file_id:
            return "photo", file_id
    return None, None


def send_local_document(chat_id, filepath, caption=""):
    """برخلاف send_attachment (که با file_id از قبل آپلودشده کار می‌کنه)، این تابع
    یک فایل واقعی از روی دیسک سرور رو آپلود و ارسال می‌کنه — برای فایل پشتیبان دیتابیس."""
    url = f"{API_URL}/sendDocument"
    try:
        with open(filepath, "rb") as f:
            files = {"document": f}
            data = {"chat_id": chat_id, "caption": caption}
            response = requests.post(url, data=data, files=files, timeout=60)
        if not response.ok:
            logger.error("ارسال فایل محلی ناموفق: %s", response.text)
            return False
        return True
    except Exception as e:
        logger.exception("خطا در ارسال فایل محلی: %s", e)
        return False


def send_attachment(chat_id, kind, file_id, caption="", keyboard=None):
    """ارسال عکس/فایل/ویس به یک چت مشخص. kind یکی از photo, document, voice."""
    endpoint = {"photo": "sendPhoto", "document": "sendDocument", "voice": "sendVoice"}.get(kind)
    field = {"photo": "photo", "document": "document", "voice": "voice"}.get(kind)
    if not endpoint:
        return False
    url = f"{API_URL}/{endpoint}"
    data = {"chat_id": chat_id, field: file_id, "caption": caption}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    try:
        response = requests.post(url, data=data, timeout=15)
        if not response.ok:
            logger.error("%s ناموفق برای %s: %s", endpoint, chat_id, response.text)
            return False
        return True
    except Exception as e:
        logger.exception("خطا در %s به %s: %s", endpoint, chat_id, e)
        return False


def forward_attachment_to_admin(kind, file_id, caption, order_id=None):
    """ارسال مدرک ضمیمه‌شده توسط مشتری برای ادمین، همراه با دکمه پاسخ."""
    keyboard = None
    if order_id:
        keyboard = make_keyboard(admin_order_action_buttons(order_id))
    return send_attachment(ADMIN_CHAT_ID, kind, file_id, caption, keyboard)


# ---------------------------------------------------------------------------
# اعتبارسنجی ورودی‌ها
# ---------------------------------------------------------------------------

def validate_phone(text):
    """شماره تلفن باید فقط رقم باشه و طول معقولی داشته باشه (۱۰ تا ۱۳ رقم)."""
    cleaned = text.strip().replace(" ", "").replace("-", "")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    return cleaned.isdigit() and 10 <= len(cleaned) <= 13


def validate_national_id(text):
    """اعتبارسنجی رسمی کد ملی ایران با الگوریتم چک‌دیجیت (استاندارد سازمان ثبت احوال).

    نکته مهم: این فقط یک اعتبارسنجی «فرمولی» آفلاینه (چک می‌کنه کد ملی از نظر ریاضی
    معتبره یا نه)، نه یک استعلام واقعی از ثبت‌احوال. برای استعلام زنده و تطبیق با
    نام/تاریخ تولد واقعی صاحب کد، نیاز به اتصال به یک سرویس رسمی (مثل شاهکار یا
    وب‌سرویس‌های احراز هویت پولی) هست که نیاز به قرارداد و مجوز جداگانه داره؛
    فعلاً چنین دسترسی‌ای نداریم.
    """
    cleaned = to_english_digits(text.strip())
    if not cleaned.isdigit() or len(cleaned) != 10:
        return False
    if cleaned == cleaned[0] * 10:
        return False  # کدهایی مثل 1111111111 از نظر فرمولی نامعتبرن
    check_digit = int(cleaned[9])
    total = sum(int(cleaned[i]) * (10 - i) for i in range(9))
    remainder = total % 11
    if remainder < 2:
        return check_digit == remainder
    return check_digit == 11 - remainder


PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


def to_english_digits(s):
    return "".join(str(PERSIAN_DIGITS.index(ch)) if ch in PERSIAN_DIGITS else ch for ch in s)


def to_persian_digits(s):
    return "".join(PERSIAN_DIGITS[int(ch)] if ch.isdigit() else ch for ch in s)


def parse_price_to_int(price_str):
    """تبدیل قیمت‌های فرمت‌شده مثل '۵۰,۰۰۰' به عدد صحیح 50000."""
    cleaned = to_english_digits(price_str).replace(",", "").strip()
    return int(cleaned) if cleaned.isdigit() else 0


def format_price(amount):
    """تبدیل عدد صحیح به رشته فارسی با کاما، مثل 50000 -> '۵۰,۰۰۰'."""
    return to_persian_digits(f"{amount:,}")


def infer_menu_target(order_type):
    """بر اساس نوع سفارش، مشخص می‌کنه دکمه «تلاش مجدد» کاربر رو به کدوم منو برگردونه."""
    if order_type.startswith("سوال"):
        return "question_menu"
    if order_type.startswith("سند"):
        return "document_menu"
    if order_type.startswith("پرونده"):
        return "case_menu"
    return "main_menu"


# ---------------------------------------------------------------------------
# منوها و متن‌ها
# ---------------------------------------------------------------------------

def show_main_menu(chat_id, name=""):
    greeting = f"سلام {name} عزیز 👋\n" if name else "سلام! 👋\n"
    keyboard = make_keyboard([
        [("🧑‍⚖️ درخواست خدمت حقوقی", "menu_services")],
        [("📂 حساب و سفارش‌های من", "menu_account")],
        [("ℹ️ راهنما و پشتیبانی", "menu_help_hub")],
    ])
    send_message(
        chat_id,
        f"{greeting}"
        "به رهیار قانون خوش اومدید ⚖️\n\n"
        "برای شروع، یکی از بخش‌های زیر رو انتخاب کنید:",
        keyboard,
    )


def show_services_menu(chat_id):
    keyboard = make_keyboard([
        [("💬 یک سوال حقوقی دارم", "menu_1")],
        [("📄 می‌خوام یک سند تنظیم کنم", "menu_2")],
        [("⚖️ پرونده‌ای برای پیگیری دارم", "menu_3")],
        [("📞 نوبت مشاوره تلفنی", "menu_consult")],
        [("🧮 محاسبه‌گر مهلت‌های قانونی", "menu_deadline_calc")],
        [("📋 مشاهده قیمت‌ها و خدمات", "menu_5")],
        [("💰 پیگیری پرداخت انجام‌شده", "menu_4")],
        [("🔙 بازگشت به منوی اصلی", "back_main")],
    ])
    send_message(
        chat_id,
        "🧑‍⚖️ درخواست خدمت حقوقی\n\n"
        "از خدمات زیر می‌تونید استفاده کنید:\n"
        "• سوال حقوقی: پاسخ مکتوب از کارشناس\n"
        "• تنظیم سند: دادخواست، قرارداد، لایحه و ...\n"
        "• پرونده: داوری یا وکالت مدنی (هزینه توافقی)\n\n"
        "کدوم رو نیاز دارید؟",
        keyboard,
    )


def show_account_menu(chat_id):
    keyboard = make_keyboard([
        [("👤 حساب کاربری من", "menu_6")],
        [("📦 وضعیت سفارش‌های من", "menu_7")],
        [("🧾 تاریخچه پرداخت‌های من", "menu_10")],
        [("⏰ یادآوری مهلت‌های قانونی", "menu_reminders")],
        [("🎫 گزارش مشکل فنی", "menu_tickets")],
        [("⚙️ دستورات و تنظیمات", "menu_8")],
        [("🔙 بازگشت به منوی اصلی", "back_main")],
    ])
    send_message(
        chat_id,
        "📂 حساب و سفارش‌های من\n\n"
        "• حساب کاربری: مشاهده و ویرایش اطلاعات شخصی\n"
        "• وضعیت سفارش‌ها: پیگیری سوال، سند یا پرونده‌تون\n"
        "• تاریخچه پرداخت: لیست همه پرداخت‌هاتون\n"
        "• تنظیمات: ریست، حذف اطلاعات و ...",
        keyboard,
    )


def show_help_hub(chat_id):
    keyboard = make_keyboard([
        [("📖 راهنمای استفاده از ربات", "menu_11")],
        [("❓ سوالات متداول", "menu_9")],
        [("🔙 بازگشت به منوی اصلی", "back_main")],
    ])
    send_message(
        chat_id,
        "ℹ️ راهنما و پشتیبانی\n\n"
        "• راهنما: توضیح کامل همه بخش‌های ربات\n"
        "• سوالات متداول: پاسخ سریع به سوال‌های رایج\n\n"
        f"برای تماس مستقیم با پشتیبانی: {SUPPORT_PHONE}",
        keyboard,
    )


def handle_question_menu(chat_id):
    keyboard = make_keyboard([
        [("💬 پرسش عمومی — ۵۰,۰۰۰ تومان", "q_general")],
        [("🎓 پرسش تخصصی — ۵۰۰,۰۰۰ تومان", "q_special")],
        [("📖 راهنما", "menu_11")],
        [("🔙 بازگشت", "menu_services")],
    ])
    send_message(
        chat_id,
        "💬 سوال حقوقی\n\n"
        "🔹 پرسش عمومی (۵۰,۰۰۰ تومان)\n"
        "مناسب برای سوال‌های ساده و راهنمایی اولیه.\n"
        "مثال: «آیا صاحب‌خونه می‌تونه وسط قرارداد اجاره رو زیاد کنه؟»\n\n"
        "🔹 پرسش تخصصی (۵۰۰,۰۰۰ تومان)\n"
        "مناسب برای موضوعات پیچیده‌تر که نیاز به بررسی مدارک دارن.\n"
        "مثال: «قراردادم رو ببینید و بگید بند فسخش قابل اجراست یا نه» (با پیوست مدرک)\n\n"
        f"⏱ زمان پاسخ‌دهی: {RESPONSE_TIME_NOTICE}\n\n"
        "کدوم نوع رو نیاز دارید؟ (اگه مطمئن نیستید، «📖 راهنما» رو بزنید)",
        keyboard,
    )


def handle_document_menu(chat_id):
    keyboard = make_keyboard([
        [("📝 دادخواست — ۶۹۷,۰۰۰ تومان", "doc_1")],
        [("📢 شکواییه — ۹۴۹,۰۰۰ تومان", "doc_2")],
        [("📃 اظهارنامه — ۴۹۷,۰۰۰ تومان", "doc_3")],
        [("📑 قرارداد — از ۷۹۹,۰۰۰ تومان", "doc_4")],
        [("⚖️ لایحه حقوقی — ۶۹۷,۰۰۰ تومان", "doc_5")],
        [("🚔 لایحه کیفری — ۹۴۹,۰۰۰ تومان", "doc_6")],
        [("📖 راهنما", "menu_11")],
        [("🔙 بازگشت", "menu_services")],
    ])
    send_message(
        chat_id,
        "📄 تنظیم سند حقوقی\n\n"
        "بعد از انتخاب نوع سند، هزینه رو پرداخت می‌کنید و اطلاعات لازم رو وارد می‌کنید. "
        f"سند شما تا {RESPONSE_TIME_NOTICE} آماده و ارسال می‌شه.\n\n"
        "🔹 دادخواست: طرح دعوی در دادگاه (مثال: مطالبه مهریه)\n"
        "🔹 شکواییه: طرح شکایت کیفری (مثال: کلاهبرداری)\n"
        "🔹 اظهارنامه: اعلام رسمی موضعی به طرف مقابل (مثال: تخلیه)\n"
        "🔹 قرارداد: تنظیم توافقنامه بین طرفین (مثال: اجاره، مشارکت)\n"
        "🔹 لایحه حقوقی: دفاع در پرونده‌های حقوقی\n"
        "🔹 لایحه کیفری: دفاع در پرونده‌های کیفری\n\n"
        "کدوم سند رو نیاز دارید؟ (اگه مطمئن نیستید، «📖 راهنما» رو بزنید)",
        keyboard,
    )


def handle_case_menu(chat_id):
    keyboard = make_keyboard([
        [("🤝 داوری", "case_arbitration")],
        [("👨‍💼 وکالت مدنی", "case_civil")],
        [("📖 راهنما", "menu_11")],
        [("🔙 بازگشت", "menu_services")],
    ])
    send_message(
        chat_id,
        "⚖️ پرونده حقوقی\n\n"
        "هزینه این خدمات بسته به موضوع و پیچیدگی پرونده، به‌صورت توافقی تعیین می‌شه. "
        "بعد از ثبت توضیحات، همکاران ما باهاتون تماس می‌گیرن.\n\n"
        "🔹 داوری: حل اختلاف خارج از دادگاه از طریق داور بی‌طرف\n"
        "🔹 وکالت مدنی: نمایندگی حقوقی در پرونده‌های مدنی\n\n"
        "کدوم نوع پرونده دارید؟",
        keyboard,
    )


def show_prices(chat_id):
    keyboard = make_keyboard([[("🔙 بازگشت به خدمات", "menu_services")]])
    send_message(
        chat_id,
        "📋 قیمت‌ها و خدمات رهیار قانون\n"
        "——————————————\n"
        "💬 سوال حقوقی\n"
        "• پرسش عمومی: ۵۰,۰۰۰ تومان\n"
        "• پرسش تخصصی: ۵۰۰,۰۰۰ تومان\n"
        "——————————————\n"
        "📄 تنظیم سند\n"
        "• دادخواست: ۶۹۷,۰۰۰ تومان\n"
        "• شکواییه: ۹۴۹,۰۰۰ تومان\n"
        "• اظهارنامه: ۴۹۷,۰۰۰ تومان\n"
        "• قرارداد: از ۷۹۹,۰۰۰ تومان\n"
        "• لایحه حقوقی: ۶۹۷,۰۰۰ تومان\n"
        "• لایحه کیفری: ۹۴۹,۰۰۰ تومان\n"
        "——————————————\n"
        "⚖️ پرونده (هزینه توافقی)\n"
        "• داوری\n"
        "• وکالت مدنی\n"
        "——————————————\n"
        f"📞 تماس مستقیم: {SUPPORT_PHONE}",
        keyboard,
    )


def show_profile(chat_id):
    user = db.get_user(chat_id)
    if not user:
        return
    text = (
        "👤 حساب کاربری من\n"
        "——————————————\n"
        f"نام: {user.get('name') or '—'}\n"
        f"نام خانوادگی: {user.get('family') or '—'}\n"
        f"شماره تماس: {user.get('phone') or '—'}\n"
        f"کد ملی: {user.get('national_id') or 'ثبت نشده'}\n"
        "——————————————\n"
        "برای ویرایش هر مورد، دکمه مربوطه رو بزنید:"
    )
    keyboard = make_keyboard([
        [("✏️ ویرایش نام", "edit_name"), ("✏️ ویرایش نام خانوادگی", "edit_family")],
        [("✏️ ویرایش شماره تماس", "edit_phone"), ("✏️ ویرایش کد ملی", "edit_national_id")],
        [("🔙 بازگشت به منوی اصلی", "back_main")],
    ])
    send_message(chat_id, text, keyboard)


ORDER_STATUS_LABELS = {
    "pending_review": "⏳ در انتظار بررسی — پرداخت دریافت شده و در نوبت بررسیه",
    "confirmed": "✅ پرداخت تایید شده — به‌زودی کار شروع می‌شه",
    "in_progress": "🔧 در حال آماده‌سازی — سند یا پاسخ دارد آماده می‌شه",
    "awaiting_customer_info": "⏸ منتظر اطلاعات شما — لطفاً به پیام کارشناس پاسخ بدید",
    "completed": "🏁 تکمیل و تحویل شده — پاسخ قبلاً ارسال شده",
    "rejected": "❌ رد شده — رسید پرداخت تایید نشد",
    "cancelled": "🚫 لغو شده",
}

ORDER_STATUS_SHORT = {
    "pending_review": "⏳ در انتظار بررسی",
    "confirmed": "✅ تایید شده",
    "in_progress": "🔧 در حال آماده‌سازی",
    "awaiting_customer_info": "⏸ منتظر اطلاعات شما",
    "completed": "🏁 تکمیل شده",
    "rejected": "❌ رد شده",
    "cancelled": "🚫 لغو شده",
}

RESPONSE_TIME_NOTICE = "معمولاً پاسخ یا سند شما تا ۷۲ ساعت کاری آماده می‌شه."
URGENT_RESPONSE_NOTICE = "⚡ چون فوری انتخاب کردید، پاسخ تا ۱۲ ساعت کاری آماده می‌شه."
DISCLAIMER_TEXT = (
    "⚠️ این راهنمایی بر اساس اطلاعاتی است که ارائه کردید. "
    "نتیجه هر پرونده به عوامل متعدد بستگی دارد و تضمین قطعی نتیجه امکان‌پذیر نیست."
)

TOPIC_LABELS = {
    "family": "👨‍👩‍👧 خانواده",
    "property": "🏠 ملکی",
    "criminal": "⚖️ کیفری",
    "contract": "📄 قرارداد/تجاری",
    "unknown": "❓ نامشخص",
}

FOLLOWUP_WINDOW_DAYS = 7
FOLLOWUP_DISCOUNT_PERCENT = 40


def ask_question_topic(chat_id, order_id):
    keyboard = make_keyboard([
        [(TOPIC_LABELS["family"], f"topic_{order_id}_family"), (TOPIC_LABELS["property"], f"topic_{order_id}_property")],
        [(TOPIC_LABELS["criminal"], f"topic_{order_id}_criminal"), (TOPIC_LABELS["contract"], f"topic_{order_id}_contract")],
        [(TOPIC_LABELS["unknown"], f"topic_{order_id}_unknown")],
    ])
    send_message(chat_id, "🏷 موضوع سوال شما به کدوم دسته نزدیک‌تره؟", keyboard)


def is_eligible_for_followup(order):
    if order["status"] != "completed" or not order["order_type"].startswith("سوال") or order.get("followup_used"):
        return False
    completed_at = db.get_order_completed_at(order["id"])
    if not completed_at:
        return False
    try:
        completed_dt = datetime.strptime(completed_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return (now_iran().replace(tzinfo=None) - completed_dt).days <= FOLLOWUP_WINDOW_DAYS


def show_my_orders(chat_id):
    orders = db.get_orders_by_chat(chat_id)
    if not orders:
        send_message(
            chat_id,
            "📦 وضعیت سفارش‌های من\n\n"
            "هنوز هیچ سفارشی ثبت نکردید.\n"
            "برای شروع، از منوی «🧑‍⚖️ درخواست خدمت حقوقی» یک خدمت رو انتخاب کنید.",
            make_keyboard([[("🔙 بازگشت به منوی اصلی", "back_main")]]),
        )
        return
    send_message(chat_id, f"📦 سفارش‌های شما ({len(orders)} مورد):\n──────────────")
    for o in orders:
        status_full = ORDER_STATUS_LABELS.get(o["status"], o["status"])
        price_part = f"\n💳 مبلغ: {o['price']} تومان" if o.get("price") else ""
        date_part = (o.get("created_at") or "").split(" ")[0]
        text = (
            f"🆔 سفارش #{o['id']}\n"
            f"📌 {o['order_type']}{price_part}\n"
            f"📅 {date_part}\n"
            f"وضعیت: {status_full}"
        )
        rows = []
        if o.get("final_reply_text") or o.get("final_reply_file_id"):
            rows.append([("📄 مشاهده پاسخ این سفارش", f"view_reply_{o['id']}")])
        if is_eligible_for_followup(o):
            rows.append([(f"💬 سوال تکمیلی ({FOLLOWUP_DISCOUNT_PERCENT}٪ تخفیف)", f"followup_{o['id']}")])
        send_message(chat_id, text, make_keyboard(rows) if rows else None)
    send_message(
        chat_id, "──────────────",
        make_keyboard([[("🔙 بازگشت به منوی اصلی", "back_main")]]),
    )


def show_payment_history(chat_id):
    orders = db.get_orders_by_chat(chat_id)
    paid_orders = [o for o in orders if o.get("price")]
    keyboard = make_keyboard([[("🔙 بازگشت به منوی اصلی", "back_main")]])
    if not paid_orders:
        send_message(
            chat_id,
            "🧾 تاریخچه پرداخت‌های من\n\nهنوز هیچ پرداختی ثبت نشده.",
            keyboard,
        )
        return
    lines = []
    for o in paid_orders:
        status_short = ORDER_STATUS_SHORT.get(o["status"], o["status"])
        date_part = (o.get("created_at") or "").split(" ")[0]
        lines.append(
            f"🆔 #{o['id']} — {o['order_type']}\n"
            f"💳 {o['price']} تومان  |  📅 {date_part}\n"
            f"وضعیت: {status_short}"
        )
    send_message(
        chat_id,
        "🧾 تاریخچه پرداخت‌های شما:\n──────────────\n\n" + "\n\n".join(lines),
        keyboard,
    )


# ---------------------------------------------------------------------------
# یادآوری مهلت‌های قانونی (بدون نیاز به محاسبه)
# ---------------------------------------------------------------------------

def show_reminders_menu(chat_id):
    reminders = db.get_reminders_by_chat(chat_id)
    lines = ["⏰ یادآوری‌های مهلت قانونی من", "──────────────"]
    rows = []
    if not reminders:
        lines.append("هنوز هیچ یادآوری‌ای ثبت نکردید.")
    for r in reminders:
        lines.append(f"📌 {r['title']}\n📅 سررسید: {r['due_date']}")
        rows.append([(f"🗑 حذف «{r['title'][:20]}»", f"delrem_{r['id']}")])
    rows.append([("➕ افزودن یادآوری جدید", "add_reminder_start")])
    rows.append([("🔙 بازگشت به منوی اصلی", "back_main")])
    send_message(chat_id, "\n\n".join(lines), make_keyboard(rows))


# ---------------------------------------------------------------------------
# نوبت‌دهی مشاوره تلفنی
# ---------------------------------------------------------------------------

def show_consultation_menu(chat_id):
    open_slots = db.get_open_consultation_slots()
    booked = db.get_booked_slots_by_chat(chat_id)
    lines = ["📞 نوبت مشاوره تلفنی", "──────────────"]
    rows = []
    if booked:
        lines.append("✅ نوبت‌های رزروشده شما:")
        for s in booked:
            lines.append(f"🕒 {s['slot_label']} — {s['price']} تومان")
        lines.append("──────────────")
    if not open_slots:
        lines.append("در حال حاضر نوبت خالی‌ای موجود نیست.")
    else:
        lines.append("نوبت‌های خالی:")
        for s in open_slots:
            rows.append([(f"🕒 {s['slot_label']} — {s['price']} تومان", f"book_slot_{s['id']}")])
    rows.append([("🔙 بازگشت به منوی اصلی", "back_main")])
    send_message(chat_id, "\n".join(lines), make_keyboard(rows))


# ---------------------------------------------------------------------------
# سیستم تیکتینگ برای مشکلات فنی
# ---------------------------------------------------------------------------

def prompt_ticket_create(chat_id):
    db.update_user(chat_id, step="ticket_create")
    send_message(
        chat_id,
        "🎫 گزارش مشکل فنی\n\nمشکلی که با ربات داشتید رو توضیح بدید:",
        back_keyboard("main_menu"),
    )


# ---------------------------------------------------------------------------
# محاسبه‌گر مهلت‌های قانونی
# ---------------------------------------------------------------------------

DEADLINE_CALC_PENDING = {}  # chat_id -> {"title": ..., "due_date": ...} — نتیجه آخرین محاسبه برای ثبت احتمالی به‌عنوان یادآوری

IRAN_WEEKLY_HOLIDAY_WEEKDAY = 4  # جمعه (Monday=0)


def show_deadline_calculator(chat_id):
    deadlines = db.get_legal_deadlines()
    if not deadlines:
        send_message(chat_id, "در حال حاضر داده‌ای برای محاسبه‌گر ثبت نشده.", back_to_menu_keyboard())
        return
    rows = [[(f"{d['title']} ({d['days']} روز)", f"calc_{d['id']}")] for d in deadlines]
    rows.append([("🔙 بازگشت به منوی اصلی", "back_main")])
    send_message(
        chat_id,
        "🧮 محاسبه‌گر مهلت‌های قانونی\n\n"
        "⚠️ این محاسبه فقط بر اساس ماده ۴۴۳ ق.آ.د.م انجام می‌شه (تعطیلات رسمی جز جمعه رو در نظر نمی‌گیره؛ "
        "برای اطمینان کامل حتماً با کارشناس هم چک کنید).\n\n"
        "نوع مهلت مدنظرتون رو انتخاب کنید:",
        make_keyboard(rows),
    )


def compute_deadline_due_date(start_date, days):
    """طبق ماده ۴۴۳: روز ابلاغ جزو مهلت نیست، پس شمارش از فردای اون روز شروع می‌شه.
    اگه روز آخر مهلت جمعه بود، به اولین روز غیرتعطیل بعدش (شنبه) منتقل می‌شه."""
    due = start_date + timedelta(days=days)
    while due.weekday() == IRAN_WEEKLY_HOLIDAY_WEEKDAY:
        due += timedelta(days=1)
    return due


def handle_deadline_date_input(chat_id, text, deadline_id):
    deadline = db.get_legal_deadline(deadline_id)
    if not deadline:
        send_message(chat_id, "⚠️ این مورد دیگه در دسترس نیست.", back_to_menu_keyboard())
        db.update_user(chat_id, step="main_menu")
        return
    normalized = to_english_digits(text.strip()).replace("/", "-")
    try:
        start_date = datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError:
        send_message(
            chat_id,
            "❌ فرمت تاریخ درست نیست. لطفاً تاریخ میلادی رو به شکل ۱۴۰۴-۰۱-۱۵ → مثال میلادی: 2026-07-18 بفرستید:",
        )
        return
    due_date = compute_deadline_due_date(start_date, deadline["days"])
    due_str = due_date.strftime("%Y-%m-%d")
    DEADLINE_CALC_PENDING[chat_id] = {"title": deadline["title"], "due_date": due_str}
    db.update_user(chat_id, step="main_menu")
    send_message(
        chat_id,
        f"🧮 نتیجه محاسبه\n\n"
        f"📌 {deadline['title']} ({deadline['legal_ref'] or ''})\n"
        f"📅 تاریخ شروع: {start_date.strftime('%Y-%m-%d')}\n"
        f"⏳ مهلت: {deadline['days']} روز\n"
        f"🔴 آخرین مهلت (تخمینی): {due_str}\n\n"
        "⚠️ این فقط یک تخمینه؛ تعطیلات رسمی غیر از جمعه در نظر گرفته نشده.",
        make_keyboard([
            [("➕ ثبت یادآوری برای این مهلت", "add_calc_reminder")],
            [("🔙 بازگشت به منوی اصلی", "back_main")],
        ]),
    )


FAQ_ITEMS = [
    ("هزینه‌ها چطور محاسبه می‌شه؟", "هزینه‌ها بر اساس نوع خدمت (سوال عمومی/تخصصی، نوع سند) از پیش مشخص و ثابته. از «📋 مشاهده قیمت‌ها و خدمات» می‌تونید کامل ببینیدشون."),
    ("چقدر طول می‌کشه جواب بگیرم؟", f"{RESPONSE_TIME_NOTICE}"),
    ("اطلاعاتم محرمانه می‌مونه؟", "بله، اطلاعات شما فقط برای ارائه خدمات حقوقی استفاده می‌شه و در اختیار شخص ثالث قرار نمی‌گیره."),
    ("چطور می‌تونم اطلاعاتم رو حذف کنم؟", "از منوی «⚙️ دستورات و تنظیمات» گزینه «🗑 حذف کامل اطلاعاتم» رو بزنید."),
    ("چطور پرداخت کنم؟", "بعد از انتخاب نوع خدمت، شماره کارت نمایش داده می‌شه؛ بعد از واریز، تصویر یا فایل رسید رو همینجا ارسال کنید."),
]


def show_user_guide(chat_id):
    keyboard = make_keyboard([
        [("💬 یک سوال حقوقی دارم", "menu_1")],
        [("🔙 بازگشت به منوی اصلی", "back_main")],
    ])
    send_message(
        chat_id,
        "📖 راهنمای استفاده از ربات رهیار قانون\n\n"
        "💬 سوال حقوقی دارم\n"
        "یک سوال بپرسید، هزینه رو پرداخت کنید، رسید رو بفرستید. می‌تونید مدارک "
        "(عکس/فایل/صدا) هم ضمیمه کنید. جواب معمولاً تا ۷۲ ساعت کاری میاد.\n\n"
        "📄 تنظیم سند\n"
        "نوع سند رو انتخاب و هزینه رو پرداخت کنید؛ بعد اطلاعات لازم برای تنظیم "
        "سند رو بنویسید.\n\n"
        "⚖️ پرونده\n"
        "برای پیگیری یک پرونده حقوقی (داوری یا وکالت مدنی)، توضیح مختصری از "
        "موضوعش بدید؛ هزینه‌ش توافقیه و همکاران باهاتون تماس می‌گیرن.\n\n"
        "💰 پیگیری پرداخت\n"
        "اگه پرداختی انجام دادید که هنوز تاییدش نیومده، از همین‌جا رسیدش رو بفرستید.\n\n"
        "📦 وضعیت سفارش‌های من\n"
        "همه سفارش‌هاتون با وضعیت فعلی (در انتظار / تایید‌شده / در حال آماده‌سازی / "
        "تکمیل‌شده) اینجا نشون داده می‌شه؛ پاسخ‌های قبلی هم از همین‌جا قابل مشاهده‌ست.\n\n"
        "🧾 تاریخچه پرداخت‌ها\n"
        "لیست همه پرداخت‌هایی که تا الان انجام دادید، با مبلغ و تاریخ.\n\n"
        "👤 حساب کاربری\n"
        "اطلاعات ثبت‌شده‌تون (نام، تلفن، کد ملی) رو می‌بینید و ویرایش می‌کنید.\n\n"
        "⚙️ دستورات و تنظیمات\n"
        "شروع مجدد ثبت‌نام، لغو کار فعلی، دیدن آیدی چتتون، یا حذف کامل اطلاعاتتون.\n\n"
        "🎟 کد تخفیف\n"
        "اگه کد تخفیف دارید، سر مرحله پرداخت (زیر شماره کارت) دکمه‌ش رو می‌بینید.\n\n"
        "📞 نوبت مشاوره تلفنی\n"
        "از منوی «درخواست خدمت حقوقی»، نوبت خالی موجود رو انتخاب و رزرو کنید.\n\n"
        "🧮 محاسبه‌گر مهلت قانونی\n"
        "نوع مهلت و تاریخ شروعش رو بدید تا آخرین مهلت تخمینی محاسبه بشه؛ می‌تونید نتیجه رو مستقیم یادآوری کنید.\n\n"
        "⏰ یادآوری مهلت‌های قانونی\n"
        "از منوی «حساب و سفارش‌های من»، یادآوری‌های خودتون رو ثبت، ببینید یا حذف کنید.\n\n"
        "🎫 گزارش مشکل فنی\n"
        "اگه با خود ربات مشکلی داشتید (نه سوال حقوقی)، از همین‌جا برای همکاران فنی گزارش کنید.\n\n"
        "⬅️ دکمه بازگشت\n"
        "توی هر مرحله‌ای که گیر کردید، دکمه «بازگشت» شما رو یک قدم عقب می‌بره.",
        keyboard,
    )


def show_faq_menu(chat_id):
    rows = [[(q, f"faq_{i}")] for i, (q, _a) in enumerate(FAQ_ITEMS)]
    rows.append([("💬 سوال دیگه‌ای دارم", "menu_1")])
    rows.append([("🔙 بازگشت به منوی اصلی", "back_main")])
    send_message(chat_id, "❓ سوالات متداول\nیکی رو انتخاب کنید:", make_keyboard(rows))


def show_faq_answer(chat_id, index):
    if not (0 <= index < len(FAQ_ITEMS)):
        show_faq_menu(chat_id)
        return
    question, answer = FAQ_ITEMS[index]
    keyboard = make_keyboard([
        [("🔙 بازگشت به سوالات متداول", "menu_9")],
        [("💬 سوال حقوقی دیگه‌ای دارم", "menu_1")],
    ])
    send_message(chat_id, f"❓ {question}\n\n{answer}", keyboard)


def show_customer_commands(chat_id):
    keyboard = make_keyboard([
        [("🔄 شروع مجدد ثبت‌نام", "cmd_reset")],
        [("⏹ لغو عملیات فعلی", "cmd_cancel")],
        [("🆔 نمایش آیدی چت من", "cmd_myid")],
        [("🗑 حذف کامل اطلاعاتم", "cmd_deletemydata")],
        [("🔙 بازگشت به منوی اصلی", "back_main")],
    ])
    send_message(
        chat_id,
        "⚙️ دستورات و تنظیمات\n\n"
        "• شروع مجدد ثبت‌نام: اطلاعاتتون رو از اول وارد می‌کنید\n"
        "• لغو عملیات فعلی: از هر مرحله‌ای گیر کردید، برمی‌گردید به منو\n"
        "• آیدی چت: برای وقتی پشتیبانی ازتون بخواد\n"
        "• حذف اطلاعات: پاک کردن کامل و دائمی حساب شما\n\n"
        "کدوم رو نیاز دارید؟",
        keyboard,
    )


PROFILE_FIELD_PROMPTS = {
    "edit_name": ("editing_name", "نام جدید خودتون رو وارد کنید:"),
    "edit_family": ("editing_family", "نام خانوادگی جدید خودتون رو وارد کنید:"),
    "edit_phone": ("editing_phone", "شماره تماس جدید خودتون رو وارد کنید (مثال: 09121234567):"),
    "edit_national_id": ("editing_national_id", "کد ملی جدید خودتون رو وارد کنید (۱۰ رقم):"),
}


def handle_profile_edit(chat_id, text, step):
    """ویرایش یکی از فیلدهای پروفایل کاربر، بعد بازگشت به نمایش پروفایل."""
    text = text.strip()

    if step == "editing_name":
        if not text:
            send_message(chat_id, "لطفاً یک نام معتبر وارد کنید:", back_keyboard("profile"))
            return
        db.update_user(chat_id, name=text, step="main_menu")
        send_message(chat_id, "✅ نام شما بروزرسانی شد.")

    elif step == "editing_family":
        if not text:
            send_message(chat_id, "لطفاً یک نام خانوادگی معتبر وارد کنید:", back_keyboard("profile"))
            return
        db.update_user(chat_id, family=text, step="main_menu")
        send_message(chat_id, "✅ نام خانوادگی شما بروزرسانی شد.")

    elif step == "editing_phone":
        if not validate_phone(text):
            send_message(chat_id, "⚠️ شماره تماس معتبر نیست. لطفاً فقط رقم وارد کنید:", back_keyboard("profile"))
            return
        db.update_user(chat_id, phone=text, step="main_menu")
        send_message(chat_id, "✅ شماره تماس شما بروزرسانی شد.")

    elif step == "editing_national_id":
        if not validate_national_id(text):
            send_message(chat_id, "⚠️ کد ملی باید دقیقاً ۱۰ رقم باشه. دوباره وارد کنید:", back_keyboard("profile"))
            return
        db.update_user(chat_id, national_id=text, step="main_menu")
        send_message(chat_id, "✅ کد ملی شما بروزرسانی شد.")

    show_profile(chat_id)


def handle_payment_follow(chat_id):
    order_id = db.create_order(chat_id, "پیگیری پرداخت")
    db.update_user(chat_id, current_order_id=order_id, step="payment_follow")
    send_message(
        chat_id,
        "💰 پیگیری پرداخت\n\n"
        "اگه مبلغی رو واریز کردید ولی هنوز تاییدیه‌ای دریافت نکردید، "
        "تصویر یا فایل رسید پرداختتون رو همینجا ارسال کنید تا بررسی بشه.\n\n"
        "📎 رسید (عکس یا فایل) رو بفرستید:",
        back_keyboard("main_menu", cancel_order=True),
    )


DOCUMENT_NAMES = {
    "doc_1": ("دادخواست", "۶۹۷,۰۰۰"),
    "doc_2": ("شکواییه", "۹۴۹,۰۰۰"),
    "doc_3": ("اظهارنامه", "۴۹۷,۰۰۰"),
    "doc_4": ("قرارداد", "۷۹۹,۰۰۰"),
    "doc_5": ("لایحه حقوقی", "۶۹۷,۰۰۰"),
    "doc_6": ("لایحه کیفری", "۹۴۹,۰۰۰"),
}


def payment_request_text(title, price):
    return (
        f"✅ «{title}» انتخاب شد\n\n"
        f"لطفاً مبلغ {price} تومان رو به شماره کارت زیر واریز کنید:\n\n"
        f"💳 {CARD_NUMBER}\n\n"
        f"سپس تصویر رسید پرداخت رو همینجا ارسال کنید:"
    )


def generate_invoice_text(order):
    """یک فاکتور ساده (متنی) بعد از تایید پرداخت، برای مشتری."""
    if not order or not order.get("price"):
        return None
    date_part = (order.get("created_at") or "").split(" ")[0]
    discount_line = ""
    if order.get("discount_amount"):
        discount_line = f"🎟 تخفیف اعمال‌شده: {format_price(order['discount_amount'])} تومان\n"
    return (
        "🧾 فاکتور\n"
        "──────────────\n"
        f"شماره سفارش: #{order['id']}\n"
        f"شرح: {order['order_type']}\n"
        f"تاریخ: {date_part}\n"
        f"{discount_line}"
        f"مبلغ پرداخت‌شده: {order['price']} تومان\n"
        "──────────────\n"
        "رهیار قانون"
    )


# ---------------------------------------------------------------------------
# ثبت‌نام
# ---------------------------------------------------------------------------

def handle_registration(chat_id, text, step):
    if step == "get_name":
        if not text.strip():
            send_message(chat_id, "لطفاً نام خودتون رو به‌صورت متنی وارد کنید:")
            return
        db.update_user(chat_id, name=text.strip(), step="get_family")
        send_message(chat_id, "عالی! حالا نام خانوادگی خودتون رو وارد کنید:", back_keyboard("get_name"))

    elif step == "get_family":
        if not text.strip():
            send_message(chat_id, "لطفاً نام خانوادگی خودتون رو وارد کنید:", back_keyboard("get_name"))
            return
        db.update_user(chat_id, family=text.strip(), step="get_phone")
        send_message(
            chat_id,
            "📞 شماره تماس خودتون رو وارد کنید (مثال: 09121234567):",
            back_keyboard("get_family"),
        )

    elif step == "get_phone":
        if not validate_phone(text):
            send_message(
                chat_id,
                "⚠️ شماره تماس واردشده معتبر نیست.\nلطفاً فقط رقم و بدون فاصله وارد کنید (مثال: 09121234567):",
                back_keyboard("get_family"),
            )
            return
        db.update_user(chat_id, phone=text.strip(), step="get_national_id")
        keyboard = back_keyboard(
            "get_phone", extra_rows=[[("رد کردن (اختیاری)", "skip_national_id")]]
        )
        send_message(chat_id, "🪪 کد ملی خودتون رو وارد کنید:\n(وارد کردن این مورد اختیاریه)", keyboard)

    elif step == "get_national_id":
        if not validate_national_id(text):
            keyboard = back_keyboard(
                "get_phone", extra_rows=[[("رد کردن (اختیاری)", "skip_national_id")]]
            )
            send_message(
                chat_id,
                "⚠️ کد ملی باید دقیقاً ۱۰ رقم باشه.\nلطفاً دوباره وارد کنید یا از دکمه «رد کردن» استفاده کنید:",
                keyboard,
            )
            return
        finish_registration(chat_id, text.strip())


def finish_registration(chat_id, national_id):
    db.update_user(chat_id, national_id=national_id, step="main_menu")
    user = db.get_user(chat_id)
    send_message(
        chat_id,
        "✅ ثبت‌نام شما با موفقیت انجام شد!\n\n"
        "💡 هر جای ربات که بودید، با دکمه «📋 منوی اصلی» پایین صفحه می‌تونید برگردید.",
        customer_persistent_keyboard(),
    )
    show_main_menu(chat_id, user.get("name", ""))
    maybe_send_welcome_gift(chat_id)


def maybe_send_welcome_gift(chat_id):
    """اگه یک کمپین «خوش‌آمدگویی» فعال باشه و این کاربر قبلاً هدیه نگرفته باشه، یک کد بهش می‌ده."""
    campaigns = db.get_active_campaigns_by_trigger("welcome")
    if not campaigns:
        return
    campaign = campaigns[0]
    code = db.assign_campaign_code_to_user(chat_id, campaign["id"])
    if code:
        send_message(
            chat_id,
            "🎁 هدیه خوش‌آمدگویی!\n\n"
            f"به‌عنوان کاربر جدید، این کد تخفیف مخصوص شماست:\n\n🎟 {code}\n"
            f"{campaign['percent_off']}٪ تخفیف روی اولین سوال یا سندتون.\n\n"
            "موقع پرداخت، از دکمه «🎟 کد تخفیف دارم» همین کد رو وارد کنید.",
        )


# ---------------------------------------------------------------------------
# مدیریت دکمه‌ها (کالبک‌ها)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ناوبری «بازگشت به مرحله قبل»
# ---------------------------------------------------------------------------

def go_to_step(chat_id, target, cancel_order=False):
    """کاربر رو به یکی از مراحل قبلی برمی‌گردونه و در صورت نیاز سفارش نیمه‌کاره رو لغو می‌کنه."""
    user = db.get_user(chat_id)
    if not user:
        return

    if cancel_order:
        order_id = user.get("current_order_id")
        if order_id:
            db.set_order_status(order_id, "cancelled")

    if target == "main_menu":
        db.update_user(chat_id, step="main_menu")
        show_main_menu(chat_id, user.get("name", ""))

    elif target == "question_menu":
        db.update_user(chat_id, step="question_menu")
        handle_question_menu(chat_id)

    elif target == "document_menu":
        db.update_user(chat_id, step="document_menu")
        handle_document_menu(chat_id)

    elif target == "case_menu":
        db.update_user(chat_id, step="case_menu")
        handle_case_menu(chat_id)

    elif target == "profile":
        db.update_user(chat_id, step="main_menu")
        show_profile(chat_id)

    elif target == "get_name":
        db.update_user(chat_id, step="get_name")
        send_message(chat_id, "نام خودتون رو وارد کنید:")

    elif target == "get_family":
        db.update_user(chat_id, step="get_family")
        send_message(chat_id, "نام خانوادگی خودتون رو وارد کنید:", back_keyboard("get_name"))

    elif target == "get_phone":
        db.update_user(chat_id, step="get_phone")
        send_message(
            chat_id,
            "📞 شماره تماس خودتون رو وارد کنید (مثال: 09121234567):",
            back_keyboard("get_family"),
        )


def handle_admin_callback(callback_data):
    """دکمه‌های تایید/رد پرداخت، پاسخ به مشتری، و منوی مدیریت که فقط ادمین می‌بینه."""
    if callback_data == "adm_menu_pending":
        show_pending_orders(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_menu_help":
        show_admin_help(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_menu_stats":
        show_stats(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_menu_monthly_report":
        send_monthly_report(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_menu_search":
        prompt_admin_search(ADMIN_CHAT_ID)
        return

    hist_match = re.match(r"^adm_hist_(.+)$", callback_data)
    if hist_match:
        show_user_full_history(ADMIN_CHAT_ID, hist_match.group(1))
        return

    if callback_data == "adm_menu_consult":
        show_admin_consult_menu(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_addslot_start":
        ADMIN_WIZARD["flow"] = "consult_slot_label"
        ADMIN_WIZARD["step"] = None
        ADMIN_WIZARD["data"] = {}
        send_message(ADMIN_CHAT_ID, "🕒 برچسب نوبت رو بنویسید (مثلاً «شنبه ۱۰ تا ۱۱ صبح»):", cancel_wizard_keyboard())
        return

    if callback_data == "adm_menu_tickets":
        show_admin_tickets(ADMIN_CHAT_ID)
        return

    ticket_close_match = re.match(r"^adm_ticket_close_(\d+)$", callback_data)
    if ticket_close_match:
        db.close_ticket(int(ticket_close_match.group(1)))
        send_message(ADMIN_CHAT_ID, "✅ تیکت بسته شد.")
        return

    if callback_data == "adm_menu_root":
        show_admin_menu(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_sub_orders":
        show_admin_orders_menu(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_sub_marketing":
        show_admin_marketing_menu(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_sub_reports":
        show_admin_reports_menu(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_menu_direct_reply":
        ADMIN_WIZARD["flow"] = "direct_reply_chatid"
        ADMIN_WIZARD["step"] = None
        ADMIN_WIZARD["data"] = {}
        send_message(
            ADMIN_CHAT_ID,
            "آیدی چت مشتری مورد نظر رو وارد کنید:\n"
            "(می‌تونید از پیام‌هایی که قبلاً چت‌آیدی توشون بود کپی کنید)",
            cancel_wizard_keyboard(),
        )
        return

    if callback_data == "adm_menu_broadcast":
        ADMIN_WIZARD["flow"] = "broadcast_content"
        ADMIN_WIZARD["step"] = None
        ADMIN_WIZARD["data"] = {}
        send_message(
            ADMIN_CHAT_ID,
            "📢 ارسال پیام همگانی\n\nمتن، عکس، فایل یا پیام صوتی که می‌خواید برای همه مشتری‌ها ارسال بشه رو بفرستید:",
            cancel_wizard_keyboard(),
        )
        return

    if callback_data == "adm_broadcast_confirm":
        data = ADMIN_WIZARD["data"]
        chat_ids = db.get_all_user_chat_ids()
        reset_admin_wizard()
        send_message(ADMIN_CHAT_ID, f"⏳ در حال ارسال به {len(chat_ids)} کاربر...")
        success, failed = 0, 0
        for cid in chat_ids:
            if cid == ADMIN_CHAT_ID:
                continue
            if data.get("attachment_type") and data.get("attachment_file_id"):
                ok = send_attachment(cid, data["attachment_type"], data["attachment_file_id"], data.get("text") or "")
            else:
                ok = send_message(cid, data.get("text", ""))
            if ok:
                success += 1
            else:
                failed += 1
            time.sleep(0.05)  # جلوگیری از فشار زیاد روی API در ارسال انبوه
        send_message(ADMIN_CHAT_ID, f"✅ پیام همگانی ارسال شد.\nموفق: {success} | ناموفق: {failed}")
        return

    if callback_data == "adm_menu_version":
        send_message(ADMIN_CHAT_ID, get_version_info_text())
        return

    if callback_data == "adm_menu_discounts":
        show_campaign_menu(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_dc_list":
        show_discount_list(ADMIN_CHAT_ID)
        return

    if callback_data.startswith("adm_dc_deactivate_"):
        code = callback_data.replace("adm_dc_deactivate_", "")
        db.deactivate_discount_code(code)
        send_message(ADMIN_CHAT_ID, f"❌ کد {code} غیرفعال شد.")
        return

    if callback_data == "adm_camp_list":
        show_campaign_list(ADMIN_CHAT_ID)
        return

    camp_toggle_match = re.match(r"^adm_camp_(pause|resume)_(\d+)$", callback_data)
    if camp_toggle_match:
        action, campaign_id = camp_toggle_match.group(1), int(camp_toggle_match.group(2))
        db.set_campaign_status(campaign_id, "paused" if action == "pause" else "active")
        send_message(ADMIN_CHAT_ID, "✅ وضعیت کمپین بروزرسانی شد.")
        return

    camp_codes_match = re.match(r"^adm_camp_codes_(\d+)$", callback_data)
    if camp_codes_match:
        show_discount_list(ADMIN_CHAT_ID, campaign_id=int(camp_codes_match.group(1)))
        return

    start_match = re.match(r"^adm_start_(\d+)$", callback_data)
    if start_match:
        order_id = int(start_match.group(1))
        order = db.get_order(order_id)
        if not order:
            send_message(ADMIN_CHAT_ID, "⚠️ سفارش موردنظر پیدا نشد.")
            return
        db.set_order_status(order_id, "in_progress")
        db.log_admin_action(order_id, "in_progress")
        send_message(
            order["chat_id"],
            f"🔧 سفارش شما (#{order_id}) در حال آماده‌سازیه.\n{RESPONSE_TIME_NOTICE}",
        )
        send_message(ADMIN_CHAT_ID, f"🔧 سفارش #{order_id} به «در حال آماده‌سازی» تغییر کرد.")
        return

    reply_match = re.match(r"^adm_reply_(\d+)$", callback_data)
    if reply_match:
        order_id = int(reply_match.group(1))
        order = db.get_order(order_id)
        if not order:
            send_message(ADMIN_CHAT_ID, "⚠️ سفارش موردنظر پیدا نشد.")
            return
        ADMIN_PENDING_REPLY["order_id"] = order_id
        send_message(
            ADMIN_CHAT_ID,
            f"✍️ حالا پیام خودتون رو بنویسید تا مستقیم برای مشتری سفارش #{order_id} ارسال بشه.\n"
            f"(برای لغو، دستور /cancel_reply رو بفرستید)",
        )
        return

    askinfo_match = re.match(r"^adm_askinfo_(\d+)$", callback_data)
    if askinfo_match:
        order_id = int(askinfo_match.group(1))
        order = db.get_order(order_id)
        if not order:
            send_message(ADMIN_CHAT_ID, "⚠️ سفارش موردنظر پیدا نشد.")
            return
        ADMIN_PENDING_ASKINFO["order_id"] = order_id
        send_message(
            ADMIN_CHAT_ID,
            f"✍️ سوالی که می‌خواید از مشتری سفارش #{order_id} بپرسید رو بنویسید.\n"
            f"(برای لغو، دستور /cancel_reply رو بفرستید)",
        )
        return

    match = re.match(r"^adm_(confirm|reject)_(\d+)$", callback_data)
    if not match:
        return
    action, order_id = match.group(1), int(match.group(2))
    order = db.get_order(order_id)
    if not order:
        send_message(ADMIN_CHAT_ID, "⚠️ سفارش موردنظر پیدا نشد.")
        return

    user_chat_id = order["chat_id"]

    if action == "confirm":
        db.set_order_status(order_id, "confirmed")
        db.log_admin_action(order_id, "confirmed")
        send_message(
            user_chat_id,
            f"✅ پرداخت شما تایید شد!\n{RESPONSE_TIME_NOTICE}",
        )
        invoice_text = generate_invoice_text(order)
        if invoice_text:
            send_message(user_chat_id, invoice_text)
        send_message(
            ADMIN_CHAT_ID,
            f"✅ سفارش #{order_id} تایید شد.",
            make_keyboard([[("🔧 شروع آماده‌سازی این سفارش", f"adm_start_{order_id}")]]),
        )
    else:
        db.set_order_status(order_id, "rejected")
        db.log_admin_action(order_id, "rejected")
        retry_target = infer_menu_target(order["order_type"])
        retry_keyboard = make_keyboard([[("🔄 تلاش مجدد", f"goback|{retry_target}|0")]])
        send_message(
            user_chat_id,
            "❌ متاسفانه رسید پرداخت شما تایید نشد.\n"
            "لطفاً از صحت واریزی مطمئن بشید و دوباره تلاش کنید، "
            f"یا برای پیگیری با {SUPPORT_PHONE} تماس بگیرید.",
            retry_keyboard,
        )
        send_message(ADMIN_CHAT_ID, f"❌ سفارش #{order_id} رد شد.")


def handle_callback(chat_id, callback_data):
    if chat_id == ADMIN_CHAT_ID and callback_data.startswith("adm_"):
        handle_admin_callback(callback_data)
        return

    if chat_id == ADMIN_CHAT_ID and callback_data.startswith("dcw_"):
        handle_discount_wizard_callback(callback_data)
        return

    if callback_data.startswith("goback|"):
        _, target, cancel_flag = callback_data.split("|")
        go_to_step(chat_id, target, cancel_order=(cancel_flag == "1"))
        return

    if callback_data.startswith("rate|"):
        _, order_id_str, score_str = callback_data.split("|")
        order_id_int, score = int(order_id_str), int(score_str)
        db.set_order_rating(order_id_int, score)
        if score < 5:
            db.update_user(chat_id, step="rating_feedback", current_order_id=order_id_int)
            send_message(
                chat_id,
                "🙏 ممنون از بازخوردتون!\n\nچی می‌تونستیم بهتر انجام بدیم؟ (اختیاری — می‌تونید رد کنید)",
                make_keyboard([[("رد کردن", "skip_feedback")]]),
            )
        else:
            send_message(chat_id, "🙏 ممنون از بازخوردتون! نظرتون برامون ارزشمنده.")
        return

    if callback_data == "skip_feedback":
        db.update_user(chat_id, step="main_menu")
        send_message(chat_id, "🙏 ممنون از وقتی که گذاشتید.")
        return

    if callback_data.startswith("review_confirm_"):
        base_step = callback_data.replace("review_confirm_", "")
        finalizer = REVIEW_FINALIZERS.get(base_step)
        if not finalizer:
            return
        user = db.get_user(chat_id)
        pending = user.get("pending_text") or ""
        finalizer(chat_id, pending)
        return

    if callback_data.startswith("review_edit_"):
        base_step = callback_data.replace("review_edit_", "")
        if base_step not in REVIEW_FINALIZERS:
            return
        db.update_user(chat_id, step=base_step, pending_text=None)
        prompt = REVIEW_REPROMPT_TEXT.get(base_step, "لطفاً متن رو دوباره بنویسید:")
        back_target = REVIEW_BACK_TARGET.get(base_step, "main_menu")
        send_message(chat_id, prompt, back_keyboard(back_target, cancel_order=True))
        return

    if callback_data.startswith("topic_"):
        topic_match = re.match(r"^topic_(\d+)_(\w+)$", callback_data)
        if topic_match:
            t_order_id, topic_key = int(topic_match.group(1)), topic_match.group(2)
            order = db.get_order(t_order_id)
            if order and order["chat_id"] == chat_id and topic_key in TOPIC_LABELS:
                db.set_order_topic(t_order_id, topic_key)
                send_message(chat_id, f"✅ ثبت شد: {TOPIC_LABELS[topic_key]}")
        return

    if callback_data.startswith("faq_"):
        show_faq_answer(chat_id, int(callback_data.replace("faq_", "")))
        return

    if callback_data.startswith("view_reply_"):
        order_id = int(callback_data.replace("view_reply_", ""))
        order = db.get_order(order_id)
        if not order or order["chat_id"] != chat_id:
            send_message(chat_id, "این پاسخ در دسترس نیست.")
            return
        if order.get("final_reply_file_id"):
            send_attachment(
                chat_id, order.get("final_reply_type") or "document",
                order["final_reply_file_id"], order.get("final_reply_text") or "",
            )
        elif order.get("final_reply_text"):
            send_message(chat_id, f"📄 پاسخ سفارش #{order_id}:\n\n{order['final_reply_text']}")
        else:
            send_message(chat_id, "هنوز پاسخی برای این سفارش ثبت نشده.")
        return

    if callback_data == "confirm_delete_data":
        db.delete_user_completely(chat_id)
        send_message(chat_id, "✅ تمام اطلاعات شما حذف شد. برای شروع دوباره، فقط یک پیام بفرستید.")
        return

    if callback_data.startswith("enter_discount_"):
        order_id = int(callback_data.replace("enter_discount_", ""))
        db.update_user(chat_id, step="entering_discount", current_order_id=order_id)
        send_message(chat_id, "🎟 کد تخفیف خودتون رو وارد کنید:")
        return

    user = db.get_user(chat_id)
    if not user:
        return

    if callback_data == "back_main":
        db.update_user(chat_id, step="main_menu")
        show_main_menu(chat_id, user.get("name", ""))

    elif callback_data == "menu_services":
        show_services_menu(chat_id)

    elif callback_data == "menu_account":
        show_account_menu(chat_id)

    elif callback_data == "menu_help_hub":
        show_help_hub(chat_id)

    elif callback_data == "skip_national_id":
        finish_registration(chat_id, "ثبت نشده")

    elif callback_data == "menu_1":
        db.update_user(chat_id, step="question_menu")
        handle_question_menu(chat_id)

    elif callback_data == "menu_2":
        db.update_user(chat_id, step="document_menu")
        handle_document_menu(chat_id)

    elif callback_data == "menu_3":
        db.update_user(chat_id, step="case_menu")
        handle_case_menu(chat_id)

    elif callback_data == "menu_4":
        handle_payment_follow(chat_id)

    elif callback_data == "menu_5":
        show_prices(chat_id)

    elif callback_data == "menu_6":
        show_profile(chat_id)

    elif callback_data == "menu_7":
        show_my_orders(chat_id)

    elif callback_data == "menu_8":
        show_customer_commands(chat_id)

    elif callback_data == "menu_9":
        show_faq_menu(chat_id)

    elif callback_data == "menu_10":
        show_payment_history(chat_id)

    elif callback_data == "menu_11":
        show_user_guide(chat_id)

    elif callback_data == "menu_reminders":
        show_reminders_menu(chat_id)

    elif callback_data == "menu_tickets":
        prompt_ticket_create(chat_id)

    elif callback_data == "menu_consult":
        show_consultation_menu(chat_id)

    elif callback_data == "menu_deadline_calc":
        show_deadline_calculator(chat_id)

    elif callback_data == "add_reminder_start":
        db.update_user(chat_id, step="reminder_add_title", pending_text=None)
        send_message(chat_id, "📝 عنوان یادآوری رو بنویسید (مثلاً «تجدیدنظر پرونده ۱۲۳»):", back_keyboard("main_menu"))

    elif callback_data.startswith("delrem_"):
        reminder_id = int(callback_data.replace("delrem_", ""))
        db.delete_reminder(reminder_id, chat_id)
        send_message(chat_id, "🗑 یادآوری حذف شد.")
        show_reminders_menu(chat_id)

    elif callback_data.startswith("book_slot_"):
        slot_id = int(callback_data.replace("book_slot_", ""))
        slot = db.get_consultation_slot(slot_id)
        if not slot:
            send_message(chat_id, "⚠️ این نوبت دیگه در دسترس نیست.")
        elif db.book_consultation_slot(slot_id, chat_id):
            user = db.get_user(chat_id)
            send_message(chat_id, f"✅ نوبت «{slot['slot_label']}» برای شما رزرو شد. کارشناس در همون بازه باهاتون تماس می‌گیره.")
            send_message(
                ADMIN_CHAT_ID,
                f"📞 نوبت مشاوره رزرو شد.\n🕒 {slot['slot_label']}\n👤 {user.get('name', '')} {user.get('family', '')}\n📞 {user.get('phone', '')}\n🆔 چت آیدی: {chat_id}",
            )
        else:
            send_message(chat_id, "⚠️ متاسفانه این نوبت همین الان توسط شخص دیگه‌ای رزرو شد.")
            show_consultation_menu(chat_id)

    elif callback_data.startswith("calc_") and callback_data != "calc_remind_noop":
        deadline_id = int(callback_data.replace("calc_", ""))
        db.update_user(chat_id, step=f"deadline_calc_date_{deadline_id}")
        send_message(
            chat_id,
            "📅 تاریخ شروع مهلت (تاریخ ابلاغ/صدور) رو به‌صورت میلادی و با فرمت YYYY-MM-DD بنویسید (مثال: 2026-07-18):",
            back_keyboard("main_menu"),
        )

    elif callback_data == "add_calc_reminder":
        pending = DEADLINE_CALC_PENDING.pop(chat_id, None)
        if not pending:
            send_message(chat_id, "⚠️ نتیجه محاسبه پیدا نشد. لطفاً دوباره محاسبه کنید.")
        else:
            db.add_reminder(chat_id, pending["title"], pending["due_date"])
            send_message(chat_id, "✅ یادآوری ثبت شد. از «⏰ یادآوری مهلت‌های قانونی» می‌تونید مدیریتش کنید.")

    elif callback_data == "cmd_reset":
        db.reset_user(chat_id)
        send_message(chat_id, "🔄 ثبت‌نام مجدد شروع شد!\n\nلطفاً نام خودتون رو وارد کنید:")

    elif callback_data == "cmd_cancel":
        db.update_user(chat_id, step="main_menu")
        send_message(chat_id, "عملیات لغو شد.")
        show_main_menu(chat_id, user.get("name", ""))

    elif callback_data == "cmd_myid":
        send_message(chat_id, f"آیدی چت شما: {chat_id}")

    elif callback_data == "cmd_deletemydata":
        keyboard = make_keyboard([
            [("✅ بله، حذف کن", "confirm_delete_data")],
            [("❌ انصراف", "back_main")],
        ])
        send_message(
            chat_id,
            "⚠️ با این کار، تمام اطلاعات شما (نام، تلفن، کد ملی، تاریخچه سفارش‌ها) "
            "برای همیشه حذف می‌شه و قابل بازگشت نیست.\n\nمطمئن هستید؟",
            keyboard,
        )

    elif callback_data in PROFILE_FIELD_PROMPTS:
        new_step, prompt = PROFILE_FIELD_PROMPTS[callback_data]
        db.update_user(chat_id, step=new_step)
        send_message(chat_id, prompt, back_keyboard("profile"))

    elif callback_data in ("q_general", "q_special"):
        is_general = callback_data == "q_general"
        q_type = "عمومی" if is_general else "تخصصی"
        keyboard = make_keyboard([
            [("🕒 عادی (تا ۷۲ ساعت کاری)", f"urg_normal_{callback_data}")],
            [("⚡ فوری (تا ۱۲ ساعت کاری، ۵۰٪ هزینه بیشتر)", f"urg_fast_{callback_data}")],
            [("🔙 بازگشت", "menu_1")],
        ])
        send_message(chat_id, f"⏱ برای پرسش {q_type}، سرعت پاسخ‌دهی مدنظرتون رو انتخاب کنید:", keyboard)

    elif callback_data.startswith("urg_"):
        _, urgency, qtype_cb = callback_data.split("_", 2)
        is_general = qtype_cb == "q_general"
        q_type = "عمومی" if is_general else "تخصصی"
        base_amount = 50000 if is_general else 500000
        is_urgent = urgency == "fast"
        amount = int(base_amount * 1.5) if is_urgent else base_amount
        q_price = format_price(amount)
        label_suffix = " ⚡ فوری" if is_urgent else ""
        order_id = db.create_order(chat_id, f"سوال {q_type}{label_suffix}", price=q_price, urgent=is_urgent)
        db.update_user(
            chat_id,
            step="waiting_payment_question",
            question_type=q_type + (" فوری" if is_urgent else ""),
            question_price=q_price,
            current_order_id=order_id,
        )
        send_message(
            chat_id,
            payment_request_text(f"پرسش {q_type}{label_suffix}", q_price),
            back_keyboard(
                "question_menu", cancel_order=True,
                extra_rows=[[("🎟 کد تخفیف دارم", f"enter_discount_{order_id}")]],
            ),
        )

    elif callback_data in DOCUMENT_NAMES:
        doc_name, doc_price = DOCUMENT_NAMES[callback_data]
        order_id = db.create_order(chat_id, f"سند {doc_name}", price=doc_price)
        db.update_user(
            chat_id,
            step="waiting_payment_document",
            document_type=doc_name,
            document_price=doc_price,
            current_order_id=order_id,
        )
        send_message(
            chat_id,
            payment_request_text(doc_name, doc_price),
            back_keyboard(
                "document_menu", cancel_order=True,
                extra_rows=[[("🎟 کد تخفیف دارم", f"enter_discount_{order_id}")]],
            ),
        )

    elif callback_data in ("case_arbitration", "case_civil"):
        case_type = "داوری" if callback_data == "case_arbitration" else "وکالت مدنی"
        order_id = db.create_order(chat_id, f"پرونده {case_type}")
        db.update_user(chat_id, step="case_detail", case_type=case_type, current_order_id=order_id)
        send_message(
            chat_id,
            f"⚖️ پرونده {case_type}\n\nلطفاً توضیح مختصری از موضوع پرونده‌تون بنویسید:",
            back_keyboard("case_menu", cancel_order=True),
        )


# ---------------------------------------------------------------------------
# مدیریت پیام‌های متنی و عکس
# ---------------------------------------------------------------------------

def back_to_menu_keyboard():
    """نگه داشته شده برای سازگاری — به back_to_main_keyboard ارجاع می‌ده."""
    return back_to_main_keyboard()


def handle_discount_entry(chat_id, code):
    """اعمال کد تخفیف روی سفارش سوال/سند در انتظار پرداخت."""
    user = db.get_user(chat_id)
    order_id = user.get("current_order_id")
    order = db.get_order(order_id) if order_id else None

    if not order or order["order_type"].startswith(("پرونده", "پیگیری")):
        send_message(chat_id, "⚠️ کد تخفیف فقط برای سوال حقوقی یا تنظیم سند قابل استفاده‌ست.")
        db.update_user(chat_id, step="main_menu")
        show_main_menu(chat_id, user.get("name", ""))
        return

    discount = db.get_discount_code(code.upper())
    if not discount or discount["used_count"] >= discount["max_uses"]:
        send_message(chat_id, "❌ این کد تخفیف معتبر نیست یا ظرفیتش تموم شده. دوباره امتحان کنید یا بدون کد ادامه بدید:")
        return

    original_amount = parse_price_to_int(order["price"])
    discounted_amount = int(original_amount * (1 - discount["percent_off"] / 100))
    new_price = format_price(discounted_amount)

    db.update_order_price(order_id, new_price)
    db.set_order_discount_amount(order_id, original_amount - discounted_amount)
    db.increment_discount_usage(code.upper())

    if order["order_type"].startswith("سوال"):
        db.update_user(chat_id, question_price=new_price, step="waiting_payment_question")
    else:
        db.update_user(chat_id, document_price=new_price, step="waiting_payment_document")

    send_message(
        chat_id,
        f"✅ کد تخفیف اعمال شد! ({discount['percent_off']}% تخفیف)\n\n"
        f"مبلغ نهایی: {new_price} تومان\n\n"
        f"لطفاً این مبلغ رو به شماره کارت زیر واریز کنید:\n\n💳 {CARD_NUMBER}\n\n"
        "سپس تصویر یا فایل رسید پرداخت رو ارسال کنید:",
    )


def finalize_case(chat_id, text):
    user = db.get_user(chat_id)
    order_id = user.get("current_order_id")
    if order_id:
        db.set_order_status(order_id, "pending_review")
    forward_ok = forward_to_admin(
        user, f"پرونده {user.get('case_type', '')}", f"📝 توضیحات: {text}", order_id=order_id
    )
    db.update_user(chat_id, step="main_menu", pending_text=None)
    if forward_ok:
        send_message(
            chat_id,
            "✅ درخواست شما با موفقیت ثبت شد.\nهمکاران ما در اسرع وقت باهاتون تماس می‌گیرن.",
            back_to_menu_keyboard(),
        )
    else:
        send_message(
            chat_id,
            "⚠️ در ثبت درخواست مشکلی پیش اومد. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.",
            back_to_menu_keyboard(),
        )


def finalize_question(chat_id, text):
    user = db.get_user(chat_id)
    order_id = user.get("current_order_id")
    if order_id:
        db.set_order_status(order_id, "pending_review")
    forward_ok = forward_to_admin(
        user, f"سوال {user.get('question_type', '')}", f"❓ سوال: {text}", order_id=order_id
    )
    db.update_user(chat_id, step="main_menu", pending_text=None)
    if forward_ok:
        send_message(
            chat_id,
            f"✅ سوال شما ثبت شد.\n{RESPONSE_TIME_NOTICE}",
            back_to_menu_keyboard(),
        )
        if order_id:
            ask_question_topic(chat_id, order_id)
    else:
        send_message(
            chat_id,
            "⚠️ در ثبت سوال مشکلی پیش اومد. لطفاً دوباره تلاش کنید.",
            back_to_menu_keyboard(),
        )


def finalize_document(chat_id, text):
    user = db.get_user(chat_id)
    order_id = user.get("current_order_id")
    if order_id:
        db.set_order_status(order_id, "pending_review")
    forward_ok = forward_to_admin(
        user, f"اطلاعات سند {user.get('document_type', '')}", f"📝 اطلاعات: {text}", order_id=order_id
    )
    db.update_user(chat_id, step="main_menu", pending_text=None)
    if forward_ok:
        send_message(
            chat_id,
            f"✅ اطلاعات شما ثبت شد.\n{RESPONSE_TIME_NOTICE}",
            back_to_menu_keyboard(),
        )
    else:
        send_message(
            chat_id,
            "⚠️ در ثبت اطلاعات مشکلی پیش اومد. لطفاً دوباره تلاش کنید.",
            back_to_menu_keyboard(),
        )


REVIEW_FINALIZERS = {
    "case_detail": finalize_case,
    "asking_question": finalize_question,
    "document_detail": finalize_document,
}

REVIEW_LABELS = {
    "case_detail": "توضیحات پرونده شما",
    "asking_question": "متن سوال شما",
    "document_detail": "اطلاعات سند شما",
}

REVIEW_REPROMPT_TEXT = {
    "case_detail": "لطفاً توضیح مختصری از موضوع پرونده‌تون بنویسید:",
    "asking_question": "لطفاً متن سوال حقوقی خودتون رو بنویسید:",
    "document_detail": "لطفاً اطلاعات لازم برای تنظیم سند رو بنویسید:",
}

REVIEW_BACK_TARGET = {
    "case_detail": "case_menu",
    "asking_question": "main_menu",
    "document_detail": "main_menu",
}


def start_review(chat_id, base_step, text):
    """قبل از ثبت نهایی، متن رو یک‌بار برای تایید مخاطب نشون می‌ده تا از اشتباه تایپی جلوگیری بشه."""
    db.update_user(chat_id, pending_text=text, step=f"{base_step}_review")
    label = REVIEW_LABELS.get(base_step, "متن شما")
    back_target = REVIEW_BACK_TARGET.get(base_step, "main_menu")
    keyboard = make_keyboard([
        [("✅ تایید و ارسال نهایی", f"review_confirm_{base_step}")],
        [("✏️ ویرایش و تغییر متن", f"review_edit_{base_step}")],
        [("🚫 انصراف از این درخواست", f"goback|{back_target}|1")],
    ])
    send_message(
        chat_id,
        f"📋 مرور نهایی\n\n"
        f"{label}:\n\n"
        f"——————————————\n"
        f"{text}\n"
        f"——————————————\n\n"
        "متن بالا درست است و می‌خواید ارسال بشه؟",
        keyboard,
    )


def handle_text_message(chat_id, text, attachment_type=None, attachment_file_id=None):
    user = db.get_user(chat_id)
    step = user.get("step", "main_menu")

    if step in ("get_name", "get_family", "get_phone", "get_national_id"):
        handle_registration(chat_id, text, step)

    elif step in ("editing_name", "editing_family", "editing_phone", "editing_national_id"):
        handle_profile_edit(chat_id, text, step)

    elif step == "entering_discount":
        handle_discount_entry(chat_id, text.strip())

    elif step == "reminder_add_title":
        if not text.strip():
            send_message(chat_id, "لطفاً عنوان یادآوری رو به‌صورت متن بنویسید:")
            return
        db.update_user(chat_id, step="reminder_add_date", pending_text=text.strip())
        send_message(
            chat_id,
            "📅 تاریخ سررسید رو به‌صورت میلادی و با فرمت YYYY-MM-DD بنویسید (مثال: 2026-07-18):",
            back_keyboard("main_menu"),
        )

    elif step == "reminder_add_date":
        normalized = to_english_digits(text.strip()).replace("/", "-")
        try:
            due_date = datetime.strptime(normalized, "%Y-%m-%d")
        except ValueError:
            send_message(chat_id, "❌ فرمت تاریخ درست نیست. لطفاً به شکل YYYY-MM-DD بفرستید (مثال: 2026-07-18):")
            return
        title = user.get("pending_text") or "یادآوری"
        db.add_reminder(chat_id, title, due_date.strftime("%Y-%m-%d"))
        db.update_user(chat_id, step="main_menu", pending_text=None)
        send_message(chat_id, "✅ یادآوری ثبت شد.", back_to_menu_keyboard())
        show_reminders_menu(chat_id)

    elif step == "ticket_create":
        if not text.strip():
            send_message(chat_id, "لطفاً مشکل خودتون رو به‌صورت متن بنویسید:")
            return
        ticket_id = db.create_ticket(chat_id, text.strip())
        db.update_user(chat_id, step="main_menu")
        send_message(chat_id, f"✅ تیکت #{ticket_id} ثبت شد. همکاران فنی به‌زودی بررسی می‌کنن.", back_to_menu_keyboard())
        send_message(ADMIN_CHAT_ID, f"🎫 تیکت فنی جدید #{ticket_id}\n👤 چت آیدی: {chat_id}\n📝 {text.strip()}")

    elif step == "rating_feedback":
        order_id = user.get("current_order_id")
        db.update_user(chat_id, step="main_menu")
        if text.strip():
            send_message(ADMIN_CHAT_ID, f"💬 بازخورد مشتری برای سفارش #{order_id}:\n\n{text.strip()}")
        send_message(chat_id, "🙏 ممنون از وقتی که گذاشتید.", back_to_menu_keyboard())

    elif step.startswith("deadline_calc_date_"):
        deadline_id = int(step.replace("deadline_calc_date_", ""))
        handle_deadline_date_input(chat_id, text, deadline_id)

    elif step.endswith("_review") and step[: -len("_review")] in REVIEW_FINALIZERS:
        base_step = step[: -len("_review")]
        if text:
            start_review(chat_id, base_step, text)
        else:
            send_message(chat_id, "لطفاً یکی از دکمه‌های بالا رو بزنید، یا متن جدیدی برای جایگزینی بفرستید.")

    elif step == "case_detail":
        order_id = user.get("current_order_id")
        if attachment_type and attachment_file_id:
            label = ATTACHMENT_LABELS.get(attachment_type, "📎 مدرک")
            forward_attachment_to_admin(
                attachment_type, attachment_file_id,
                f"{label} — پرونده {user.get('case_type', '')}", order_id=order_id,
            )
            if not text:
                send_message(
                    chat_id,
                    "✅ مدرک دریافت شد.\nمی‌تونید مدرک دیگری بفرستید یا توضیح متنی پرونده رو بنویسید تا ثبت نهایی بشه:",
                    back_keyboard("case_menu", cancel_order=True),
                )
                return
        if not text:
            send_message(
                chat_id,
                "لطفاً توضیح مختصری از موضوع پرونده‌تون بنویسید:",
                back_keyboard("case_menu", cancel_order=True),
            )
            return
        start_review(chat_id, "case_detail", text)

    elif step == "waiting_payment_question":
        if attachment_type not in ("photo", "document") or not attachment_file_id:
            send_message(
                chat_id,
                "📎 لطفاً تصویر یا فایل رسید پرداخت رو ارسال کنید:",
                back_keyboard("question_menu", cancel_order=True),
            )
            return
        order_id = user.get("current_order_id")
        caption = f"رسید پرداخت — سوال {user.get('question_type', '')} — {user.get('question_price', '')} تومان"
        sent = forward_receipt_to_admin(attachment_type, attachment_file_id, caption, order_id)
        if sent:
            db.attach_receipt(order_id, attachment_file_id)
            db.update_user(chat_id, step="asking_question")
            send_message(
                chat_id,
                "✅ رسید دریافت شد!\n\nحالا سوال حقوقی خودتون رو بنویسید. "
                "می‌تونید مدارک مرتبط (عکس، فایل، پیام صوتی) رو هم همراه یا جدا ارسال کنید:",
                back_keyboard("main_menu", cancel_order=True),
            )
        else:
            send_message(
                chat_id,
                "⚠️ ارسال رسید با مشکل مواجه شد. لطفاً چند لحظه دیگه دوباره تصویر رسید رو ارسال کنید.",
                back_keyboard("question_menu", cancel_order=True),
            )

    elif step == "asking_question":
        order_id = user.get("current_order_id")
        if attachment_type and attachment_file_id:
            label = ATTACHMENT_LABELS.get(attachment_type, "📎 مدرک")
            forward_attachment_to_admin(
                attachment_type, attachment_file_id,
                f"{label} — سوال {user.get('question_type', '')}", order_id=order_id,
            )
            if not text:
                send_message(
                    chat_id,
                    "✅ مدرک دریافت شد.\nمی‌تونید مدرک دیگری بفرستید یا متن سوال رو بنویسید تا ثبت نهایی بشه:",
                    back_keyboard("main_menu", cancel_order=True),
                )
                return
        if not text:
            send_message(
                chat_id,
                "لطفاً متن سوال حقوقی خودتون رو بنویسید:",
                back_keyboard("main_menu", cancel_order=True),
            )
            return
        start_review(chat_id, "asking_question", text)

    elif step == "waiting_payment_document":
        if attachment_type not in ("photo", "document") or not attachment_file_id:
            send_message(
                chat_id,
                "📎 لطفاً تصویر یا فایل رسید پرداخت رو ارسال کنید:",
                back_keyboard("document_menu", cancel_order=True),
            )
            return
        order_id = user.get("current_order_id")
        caption = f"رسید پرداخت — {user.get('document_type', '')} — {user.get('document_price', '')} تومان"
        sent = forward_receipt_to_admin(attachment_type, attachment_file_id, caption, order_id)
        if sent:
            db.attach_receipt(order_id, attachment_file_id)
            db.update_user(chat_id, step="document_detail")
            send_message(
                chat_id,
                "✅ رسید دریافت شد!\n\nحالا اطلاعات لازم برای تنظیم سند رو بنویسید. "
                "می‌تونید مدارک مرتبط (عکس، فایل، پیام صوتی) رو هم ارسال کنید:",
                back_keyboard("main_menu", cancel_order=True),
            )
        else:
            send_message(
                chat_id,
                "⚠️ ارسال رسید با مشکل مواجه شد. لطفاً چند لحظه دیگه دوباره تصویر رسید رو ارسال کنید.",
                back_keyboard("document_menu", cancel_order=True),
            )

    elif step == "document_detail":
        order_id = user.get("current_order_id")
        if attachment_type and attachment_file_id:
            label = ATTACHMENT_LABELS.get(attachment_type, "📎 مدرک")
            forward_attachment_to_admin(
                attachment_type, attachment_file_id,
                f"{label} — سند {user.get('document_type', '')}", order_id=order_id,
            )
            if not text:
                send_message(
                    chat_id,
                    "✅ مدرک دریافت شد.\nمی‌تونید مدرک دیگری بفرستید یا اطلاعات سند رو بنویسید تا ثبت نهایی بشه:",
                    back_keyboard("main_menu", cancel_order=True),
                )
                return
        if not text:
            send_message(
                chat_id,
                "لطفاً اطلاعات لازم برای تنظیم سند رو بنویسید:",
                back_keyboard("main_menu", cancel_order=True),
            )
            return
        start_review(chat_id, "document_detail", text)

    elif step == "providing_more_info":
        order_id = user.get("current_order_id")
        order = db.get_order(order_id) if order_id else None
        if attachment_type and attachment_file_id:
            label = ATTACHMENT_LABELS.get(attachment_type, "📎 مدرک")
            forward_attachment_to_admin(
                attachment_type, attachment_file_id,
                f"{label} — اطلاعات تکمیلی سفارش #{order_id}", order_id=order_id,
            )
        if text:
            forward_to_admin(user, f"اطلاعات تکمیلی سفارش #{order_id}", f"📝 {text}", order_id=order_id)
        if not text and not (attachment_type and attachment_file_id):
            send_message(chat_id, "لطفاً پاسخ خودتون رو به‌صورت متن، عکس، فایل یا پیام صوتی بفرستید:")
            return
        if order and order["status"] == "awaiting_customer_info":
            db.set_order_status(order_id, "in_progress")
            db.log_admin_action(order_id, "customer_replied")
        db.update_user(chat_id, step="main_menu")
        send_message(
            chat_id,
            "✅ اطلاعات شما برای کارشناس ارسال شد. ممنون از همکاریتون.",
            back_to_menu_keyboard(),
        )

    elif step == "payment_follow":
        if attachment_type not in ("photo", "document") or not attachment_file_id:
            send_message(
                chat_id,
                "📎 لطفاً تصویر یا فایل رسید پرداخت رو ارسال کنید:",
                back_keyboard("main_menu", cancel_order=True),
            )
            return
        order_id = user.get("current_order_id")
        sent = forward_receipt_to_admin(attachment_type, attachment_file_id, "رسید پیگیری پرداخت", order_id)
        if sent:
            db.attach_receipt(order_id, attachment_file_id)
            db.update_user(chat_id, step="main_menu")
            send_message(
                chat_id,
                "✅ رسید شما دریافت شد و به‌زودی بررسی می‌شه.",
                back_to_menu_keyboard(),
            )
        else:
            send_message(
                chat_id,
                "⚠️ ارسال رسید با مشکل مواجه شد. لطفاً چند لحظه دیگه دوباره تلاش کنید.",
            )

    else:
        show_main_menu(chat_id, user.get("name", ""))


def show_admin_menu(chat_id):
    keyboard = make_keyboard([
        [("🗂 سفارش‌ها و مشتری‌ها", "adm_sub_orders")],
        [("📢 بازاریابی و اطلاع‌رسانی", "adm_sub_marketing")],
        [("📊 آمار، گزارش و راهنما", "adm_sub_reports")],
    ])
    send_message(
        chat_id,
        "🛠 پنل مدیریت رهیار قانون\n\nیکی از بخش‌ها رو انتخاب کنید:",
        keyboard,
    )
    send_message(
        chat_id,
        "برای دسترسی سریع، از دکمه پایین صفحه هم می‌تونید استفاده کنید.",
        admin_persistent_keyboard(),
    )


def show_admin_orders_menu(chat_id):
    keyboard = make_keyboard([
        [("📋 سفارش‌های در انتظار بررسی", "adm_menu_pending")],
        [("🔍 جستجوی مشتری", "adm_menu_search")],
        [("✉️ ارسال پیام مستقیم", "adm_menu_direct_reply")],
        [("📞 نوبت‌های مشاوره تلفنی", "adm_menu_consult")],
        [("🎫 تیکت‌های فنی باز", "adm_menu_tickets")],
        [("🔙 بازگشت به پنل مدیریت", "adm_menu_root")],
    ])
    send_message(
        chat_id,
        "🗂 سفارش‌ها و مشتری‌ها\n\n"
        "• سفارش‌های در انتظار: بررسی، تایید یا رد پرداخت‌ها\n"
        "• جستجوی مشتری: پیدا کردن با نام یا تلفن\n"
        "• پیام مستقیم: ارسال پیام به یک مشتری خاص\n"
        "• نوبت مشاوره: افزودن نوبت خالی و دیدن رزروها\n"
        "• تیکت‌های فنی: مشکلات گزارش‌شده توسط مشتری‌ها",
        keyboard,
    )


def show_admin_marketing_menu(chat_id):
    keyboard = make_keyboard([
        [("🎯 مدیریت کمپین‌ها", "adm_menu_discounts")],
        [("📢 ارسال پیام همگانی", "adm_menu_broadcast")],
        [("🔙 بازگشت به پنل مدیریت", "adm_menu_root")],
    ])
    send_message(
        chat_id,
        "📢 بازاریابی و اطلاع‌رسانی\n\n"
        "• کمپین‌ها: ساخت کد تخفیف (دستی، خوش‌آمدگویی و ...)\n"
        "• پیام همگانی: ارسال اطلاعیه یا پیشنهاد به همه مشتری‌ها",
        keyboard,
    )


def show_admin_reports_menu(chat_id):
    keyboard = make_keyboard([
        [("📊 آمار", "adm_menu_stats")],
        [("📅 گزارش مالی ماهانه", "adm_menu_monthly_report")],
        [("📖 راهنمای دستورات", "adm_menu_help")],
        [("ℹ️ نسخه ربات", "adm_menu_version")],
        [("🔙 بازگشت به پنل مدیریت", "adm_menu_root")],
    ])
    send_message(
        chat_id,
        "📊 آمار، گزارش و راهنما\n\n"
        "• آمار: تعداد سفارش و کاربر امروز و این هفته\n"
        "• راهنمای دستورات: همه کارهایی که می‌شه از پنل انجام داد\n"
        "• هر روز صبح ساعت ۹ یک گزارش خودکار دریافت می‌کنید",
        keyboard,
    )


def show_stats(chat_id):
    stats = db.get_stats()
    send_message(
        chat_id,
        "📊 آمار ربات رهیار قانون\n\n"
        f"🆕 سفارش‌های امروز: {stats['today_orders']}\n"
        f"📅 سفارش‌های این هفته: {stats['week_orders']}\n"
        f"✅ سفارش‌های تایید شده: {stats['confirmed_orders']}\n"
        f"⏳ سفارش‌های در انتظار: {stats['pending_orders']}\n\n"
        f"👥 کاربران جدید امروز: {stats['today_users']}\n"
        f"👥 کل کاربران: {stats['total_users']}",
    )


def prompt_admin_search(chat_id):
    ADMIN_WIZARD["flow"] = "search"
    ADMIN_WIZARD["step"] = None
    ADMIN_WIZARD["data"] = {}
    send_message(chat_id, "🔍 نام، نام خانوادگی یا شماره تلفن مشتری رو بفرستید:", cancel_wizard_keyboard())


def show_search_results(chat_id, query):
    results = db.search_users(query)
    if not results:
        send_message(chat_id, f"چیزی برای «{query}» پیدا نشد.")
        return
    for u in results:
        text = (
            f"👤 {u.get('name', '')} {u.get('family', '')}\n"
            f"📞 {u.get('phone', '')}\n"
            f"🆔 چت آیدی: {u['chat_id']}"
        )
        send_message(chat_id, text, make_keyboard([[("📜 تاریخچه کامل تعاملات", f"adm_hist_{u['chat_id']}")]]))


def show_user_full_history(chat_id, target_chat_id):
    summary = db.get_user_order_summary(target_chat_id)
    u = summary["user"]
    if not u:
        send_message(chat_id, "⚠️ این مشتری دیگه در دیتابیس پیدا نشد.")
        return
    lines = [
        f"📜 تاریخچه کامل — {u.get('name', '')} {u.get('family', '')}",
        f"📞 {u.get('phone', '')}  |  🆔 {target_chat_id}",
        f"🪪 کد ملی: {u.get('national_id') or 'ثبت نشده'}",
        f"📦 تعداد سفارش‌ها: {summary['order_count']}",
        f"💰 مجموع پرداختی: {format_price(summary['total_paid'])} تومان",
        "──────────────",
    ]
    for o in summary["orders"]:
        date_part = (o.get("created_at") or "").split(" ")[0]
        lines.append(
            f"#{o['id']} — {o['order_type']} — {o.get('price') or '—'} تومان — "
            f"{ORDER_STATUS_SHORT.get(o['status'], o['status'])} — {date_part}"
        )
    send_message(chat_id, "\n".join(lines))


# ---------------------------------------------------------------------------
# مدیریت نوبت‌های مشاوره تلفنی (ادمین)
# ---------------------------------------------------------------------------

def show_admin_consult_menu(chat_id):
    open_slots = db.get_open_consultation_slots()
    lines = ["📞 نوبت‌های مشاوره تلفنی", "──────────────"]
    if not open_slots:
        lines.append("در حال حاضر نوبت خالی‌ای ثبت نشده.")
    else:
        for s in open_slots:
            lines.append(f"🆔 #{s['id']} — {s['slot_label']} — {s['price']} تومان — باز")
    send_message(
        chat_id, "\n".join(lines),
        make_keyboard([
            [("➕ افزودن نوبت جدید", "adm_addslot_start")],
            [("🔙 بازگشت", "adm_sub_orders")],
        ]),
    )


# ---------------------------------------------------------------------------
# مدیریت تیکت‌های فنی (ادمین)
# ---------------------------------------------------------------------------

def show_admin_tickets(chat_id):
    tickets = db.get_open_tickets()
    if not tickets:
        send_message(chat_id, "✅ در حال حاضر تیکت باز فنی‌ای وجود نداره.", make_keyboard([[("🔙 بازگشت", "adm_sub_orders")]]))
        return
    for t in tickets:
        text = f"🎫 تیکت #{t['id']}\n👤 چت آیدی: {t['chat_id']}\n📝 {t['message']}\n🕒 {t['created_at']}"
        send_message(chat_id, text, make_keyboard([[("✅ بستن تیکت", f"adm_ticket_close_{t['id']}")]]))


def show_pending_orders(chat_id):
    orders = db.get_pending_orders(limit=10)
    if not orders:
        send_message(chat_id, "✅ در حال حاضر سفارش در انتظار بررسی‌ای وجود نداره.")
        return
    for order in orders:
        text = (
            f"🆔 سفارش #{order['id']}\n"
            f"📌 نوع: {order['order_type']}\n"
            f"💳 مبلغ: {order.get('price') or '—'}\n"
            f"👤 چت مشتری: {order['chat_id']}\n"
            f"🕒 تاریخ: {order['created_at']}"
        )
        keyboard = make_keyboard([
            [
                ("✅ تایید", f"adm_confirm_{order['id']}"),
                ("❌ رد", f"adm_reject_{order['id']}"),
            ],
            *admin_order_action_buttons(order['id']),
        ])
        send_message(chat_id, text, keyboard)


# ---------------------------------------------------------------------------
# ویزارد ساخت کد تخفیف (چندمرحله‌ای، فقط با دکمه)
# ---------------------------------------------------------------------------

UNLIMITED_USES = 1_000_000_000


def percent_keyboard():
    return make_keyboard([
        [("۵٪", "dcw_percent_5"), ("۱۰٪", "dcw_percent_10"), ("۱۵٪", "dcw_percent_15")],
        [("۲۰٪", "dcw_percent_20"), ("۳۰٪", "dcw_percent_30"), ("۵۰٪", "dcw_percent_50")],
        [("✏️ عدد دلخواه", "dcw_percent_custom")],
        [("❌ لغو", "dcw_cancel")],
    ])


def maxuses_keyboard():
    return make_keyboard([
        [("۱ بار", "dcw_maxuses_1"), ("۵ بار", "dcw_maxuses_5"), ("۱۰ بار", "dcw_maxuses_10")],
        [("۵۰ بار", "dcw_maxuses_50"), ("♾ نامحدود", "dcw_maxuses_unlimited")],
        [("✏️ عدد دلخواه", "dcw_maxuses_custom")],
        [("❌ لغو", "dcw_cancel")],
    ])


def quantity_keyboard():
    return make_keyboard([
        [("۱ کد", "dcw_qty_1"), ("۵ کد", "dcw_qty_5"), ("۱۰ کد", "dcw_qty_10")],
        [("۲۰ کد", "dcw_qty_20"), ("۵۰ کد", "dcw_qty_50")],
        [("✏️ عدد دلخواه", "dcw_qty_custom")],
        [("❌ لغو", "dcw_cancel")],
    ])


def trigger_type_keyboard():
    return make_keyboard([
        [("✋ دستی", "dcw_trigger_manual")],
        [("🎁 خوش‌آمدگویی کاربر جدید", "dcw_trigger_welcome")],
        [("🤝 دعوت از دوستان (به‌زودی)", "dcw_trigger_invite")],
        [("❌ لغو", "dcw_cancel")],
    ])


def autorefill_keyboard():
    return make_keyboard([
        [("✅ روشن (خودش تمدید کنه)", "dcw_autorefill_on")],
        [("❌ خاموش (وقتی تموم شد بهم بگو)", "dcw_autorefill_off")],
        [("❌ لغو", "dcw_cancel")],
    ])


TRIGGER_LABELS = {
    "manual": "✋ دستی",
    "welcome": "🎁 خوش‌آمدگویی",
    "invite": "🤝 دعوت از دوستان",
}


def start_discount_wizard(chat_id):
    ADMIN_WIZARD["flow"] = "campaign_create"
    ADMIN_WIZARD["step"] = "trigger_type"
    ADMIN_WIZARD["data"] = {}
    send_message(
        chat_id,
        "🎯 ساخت کمپین جدید\n\n"
        "مرحله ۱ از ۶\n"
        "این کمپین چه زمانی باید کد بده؟",
        trigger_type_keyboard(),
    )


def show_discount_confirmation(chat_id):
    data = ADMIN_WIZARD["data"]
    max_uses_label = "نامحدود" if data["max_uses"] >= UNLIMITED_USES else f"{data['max_uses']} بار"
    autorefill_label = "✅ روشن" if data["auto_refill"] else "❌ خاموش"
    text = (
        "📋 خلاصه کمپین جدید\n\n"
        f"نوع: {TRIGGER_LABELS.get(data['trigger_type'], data['trigger_type'])}\n"
        f"نام: {data['name']}\n"
        f"درصد تخفیف: {data['percent']}٪\n"
        f"سقف استفاده هر کد: {max_uses_label}\n"
        f"تعداد کد در هر استخر: {data['quantity']}\n"
        f"تکمیل خودکار استخر: {autorefill_label}\n\n"
        "تایید می‌کنید؟"
    )
    keyboard = make_keyboard([[("✅ تایید و ساخت", "dcw_confirm"), ("❌ لغو", "dcw_cancel")]])
    ADMIN_WIZARD["step"] = "confirm"
    send_message(chat_id, text, keyboard)


def handle_discount_wizard_text(chat_id, text):
    step = ADMIN_WIZARD["step"]
    data = ADMIN_WIZARD["data"]
    text = text.strip()

    if step == "name":
        if not text:
            send_message(chat_id, "لطفاً یک اسم معتبر وارد کنید:", cancel_wizard_keyboard())
            return
        data["name"] = text
        ADMIN_WIZARD["step"] = "percent"
        send_message(chat_id, "مرحله ۳ از ۶\nچند درصد تخفیف؟", percent_keyboard())

    elif step == "percent_custom":
        if not text.isdigit() or not (1 <= int(text) <= 100):
            send_message(chat_id, "لطفاً یک عدد بین ۱ تا ۱۰۰ وارد کنید:", cancel_wizard_keyboard())
            return
        data["percent"] = int(text)
        ADMIN_WIZARD["step"] = "maxuses"
        send_message(chat_id, "مرحله ۴ از ۶\nهر کد چند بار قابل استفاده باشه؟", maxuses_keyboard())

    elif step == "maxuses_custom":
        if not text.isdigit() or int(text) < 1:
            send_message(chat_id, "لطفاً یک عدد صحیح مثبت وارد کنید:", cancel_wizard_keyboard())
            return
        data["max_uses"] = int(text)
        ADMIN_WIZARD["step"] = "quantity"
        send_message(chat_id, "مرحله ۵ از ۶\nهر استخر چند تا کد داشته باشه؟", quantity_keyboard())

    elif step == "quantity_custom":
        if not text.isdigit() or int(text) < 1:
            send_message(chat_id, "لطفاً یک عدد صحیح مثبت وارد کنید:", cancel_wizard_keyboard())
            return
        data["quantity"] = int(text)
        ADMIN_WIZARD["step"] = "autorefill"
        send_message(
            chat_id,
            "مرحله ۶ از ۶\nوقتی کدهای استخر تموم شد، خودش دسته جدید بسازه؟",
            autorefill_keyboard(),
        )


def handle_discount_wizard_callback(callback_data):
    data = ADMIN_WIZARD["data"]

    if callback_data == "dcw_cancel":
        reset_admin_wizard()
        send_message(ADMIN_CHAT_ID, "لغو شد.")
        return

    if callback_data == "dcw_start":
        start_discount_wizard(ADMIN_CHAT_ID)
        return

    if callback_data.startswith("dcw_trigger_"):
        trigger = callback_data.replace("dcw_trigger_", "")
        if trigger == "invite":
            send_message(
                ADMIN_CHAT_ID,
                "🤝 کمپین «دعوت از دوستان» هنوز آماده نیست و به‌زودی اضافه می‌شه.\n"
                "لطفاً یکی از گزینه‌های دیگه رو انتخاب کنید:",
                trigger_type_keyboard(),
            )
            return
        data["trigger_type"] = trigger
        ADMIN_WIZARD["step"] = "name"
        default_names = {"manual": "کمپین دستی", "welcome": "خوش‌آمدگویی"}
        send_message(
            ADMIN_CHAT_ID,
            f"مرحله ۲ از ۶\nیک اسم برای این کمپین انتخاب کنید (مثلاً «{default_names.get(trigger, 'کمپین من')}»):",
            cancel_wizard_keyboard(),
        )
        return

    if callback_data.startswith("dcw_percent_"):
        value = callback_data.replace("dcw_percent_", "")
        if value == "custom":
            ADMIN_WIZARD["step"] = "percent_custom"
            send_message(ADMIN_CHAT_ID, "درصد تخفیف رو به‌صورت عدد وارد کنید (مثلاً 12):", cancel_wizard_keyboard())
            return
        data["percent"] = int(value)
        ADMIN_WIZARD["step"] = "maxuses"
        send_message(ADMIN_CHAT_ID, "مرحله ۴ از ۶\nهر کد چند بار قابل استفاده باشه؟", maxuses_keyboard())
        return

    if callback_data.startswith("dcw_maxuses_"):
        value = callback_data.replace("dcw_maxuses_", "")
        if value == "custom":
            ADMIN_WIZARD["step"] = "maxuses_custom"
            send_message(ADMIN_CHAT_ID, "سقف استفاده هر کد رو به‌صورت عدد وارد کنید:", cancel_wizard_keyboard())
            return
        data["max_uses"] = UNLIMITED_USES if value == "unlimited" else int(value)
        ADMIN_WIZARD["step"] = "quantity"
        send_message(ADMIN_CHAT_ID, "مرحله ۵ از ۶\nهر استخر چند تا کد داشته باشه؟", quantity_keyboard())
        return

    if callback_data.startswith("dcw_qty_"):
        value = callback_data.replace("dcw_qty_", "")
        if value == "custom":
            ADMIN_WIZARD["step"] = "quantity_custom"
            send_message(ADMIN_CHAT_ID, "تعداد کد رو به‌صورت عدد وارد کنید:", cancel_wizard_keyboard())
            return
        data["quantity"] = int(value)
        ADMIN_WIZARD["step"] = "autorefill"
        send_message(
            ADMIN_CHAT_ID,
            "مرحله ۶ از ۶\nوقتی کدهای استخر تموم شد، خودش دسته جدید بسازه؟",
            autorefill_keyboard(),
        )
        return

    if callback_data in ("dcw_autorefill_on", "dcw_autorefill_off"):
        data["auto_refill"] = callback_data == "dcw_autorefill_on"
        show_discount_confirmation(ADMIN_CHAT_ID)
        return

    if callback_data == "dcw_confirm":
        campaign_id = db.create_campaign(
            data["name"], data["trigger_type"], data["percent"],
            data["max_uses"], data["quantity"], data["auto_refill"],
        )
        reset_admin_wizard()
        stats = db.get_campaign_code_stats(campaign_id)
        send_message(
            ADMIN_CHAT_ID,
            f"✅ کمپین «{data['name']}» ساخته شد و {stats['total']} کد اولیه توی استخرشه.\n\n"
            "برای دیدن یا پخش کردن کدها، از «📋 لیست کمپین‌ها» → «🔑 مشاهده کدها» استفاده کنید.",
        )
        return


def show_campaign_menu(chat_id):
    keyboard = make_keyboard([
        [("➕ ساخت کمپین جدید", "dcw_start")],
        [("📋 لیست کمپین‌ها", "adm_camp_list")],
        [("🔙 بازگشت", "adm_menu_root")],
    ])
    send_message(chat_id, "🎯 مدیریت کمپین‌ها", keyboard)


def show_campaign_list(chat_id):
    campaigns = db.get_campaigns()
    if not campaigns:
        send_message(chat_id, "هنوز هیچ کمپینی نساختید.")
        return
    for c in campaigns:
        stats = db.get_campaign_code_stats(c["id"])
        status_label = "🟢 فعال" if c["status"] == "active" else "🔴 متوقف"
        text = (
            f"🎯 {c['name']}\n"
            f"نوع: {TRIGGER_LABELS.get(c['trigger_type'], c['trigger_type'])}\n"
            f"وضعیت: {status_label}\n"
            f"تخفیف: {c['percent_off']}٪ | سقف هر کد: {c['max_uses_per_code']}\n"
            f"کدها: {stats['total']} کل | {stats['unassigned']} آزاد | {stats['used']} استفاده‌شده"
        )
        if c["status"] == "active":
            toggle = ("⏸ متوقف کن", f"adm_camp_pause_{c['id']}")
        else:
            toggle = ("▶️ فعال کن", f"adm_camp_resume_{c['id']}")
        keyboard = make_keyboard([[toggle], [("🔑 مشاهده کدها", f"adm_camp_codes_{c['id']}")]])
        send_message(chat_id, text, keyboard)


def show_discount_list(chat_id, campaign_id=None):
    codes = db.list_active_discount_codes(campaign_id=campaign_id)
    if not codes:
        send_message(chat_id, "هیچ کد فعالی وجود نداره.")
        return
    for c in codes:
        max_label = "نامحدود" if c["max_uses"] >= UNLIMITED_USES else str(c["max_uses"])
        label = c.get("name") or c.get("description") or ""
        assigned = f"\n👤 اختصاص‌یافته به: {c['assigned_to']}" if c.get("assigned_to") else ""
        text = (
            f"🎟 {c['code']} — {label}\n"
            f"{c['percent_off']}٪ تخفیف | استفاده‌شده: {c['used_count']} از {max_label}{assigned}"
        )
        keyboard = make_keyboard([[("❌ غیرفعال کن", f"adm_dc_deactivate_{c['code']}")]])
        send_message(chat_id, text, keyboard)


def show_admin_help(chat_id):
    send_message(
        chat_id,
        "📖 راهنمای مدیریت ربات\n\n"
        "همه چیز از منوی مدیریت با دکمه قابل انجامه — کافیه «🛠 پنل مدیریت» رو بزنید:\n\n"
        "📋 سفارش‌های در انتظار — لیست و بررسی سفارش‌های جدید\n"
        "📊 آمار — وضعیت کلی ربات\n"
        "📅 گزارش مالی ماهانه — درآمد و مجموع تخفیف کمپین‌های ماه قبل\n"
        "🔍 جستجوی مشتری — پیدا کردن یک مشتری با اسم یا تلفن + تاریخچه کامل تعاملاتش\n"
        "✉️ ارسال پیام مستقیم — پیام به یک آیدی چت خاص\n"
        "📞 نوبت‌های مشاوره تلفنی — افزودن نوبت خالی و دیدن رزروها\n"
        "🎫 تیکت‌های فنی باز — مشکلات گزارش‌شده توسط مشتری‌ها\n"
        "🎯 مدیریت کمپین‌ها — ساخت کمپین (دستی/خوش‌آمدگویی)، مشاهده و توقف/فعال‌سازی\n"
        "📢 ارسال پیام همگانی — پیام به همه مشتری‌ها یکجا\n"
        "ℹ️ نسخه ربات — دیدن شماره نسخه فعلی\n\n"
        "📊 هر روز صبح ساعت ۹ یک گزارش خودکار دیروز رو دریافت می‌کنید.\n"
        "📅 روز اول هر ماه، گزارش مالی ماه قبل هم خودکار ارسال می‌شه.\n"
        "💾 هر هفته یک نسخه پشتیبان از دیتابیس براتون ارسال می‌شه.\n\n"
        "💡 برای پاسخ سریع به یک سفارش خاص، زیر همون سفارش دکمه «📩 پاسخ به این مشتری» هست — "
        "بزنید و بعد متن، عکس، فایل یا پیام صوتی خودتون رو بفرستید؛ مستقیم برای همون مشتری ارسال می‌شه.\n"
        "💡 دکمه «❓ درخواست اطلاعات بیشتر» هم برای وقتیه که قبل از پاسخ نهایی نیاز به توضیح بیشتری از مشتری دارید.\n\n"
        "دستورات متنی هم هنوز کار می‌کنن اگه بخواید:\n"
        "/admin ، /stats ، /find، /cancel_reply\n"
        "/addmoad عنوان|تعداد روز|مرجع قانونی — افزودن مورد جدید به محاسبه‌گر مهلت قانونی",
    )


# ---------------------------------------------------------------------------
# ورودی اصلی پیام‌ها
# ---------------------------------------------------------------------------

def handle_admin_message(chat_id, text, attachment_type=None, attachment_file_id=None):
    """پیام‌های ادمین: ویزاردهای فعال، حالت پاسخ به مشتری، منوی مدیریت، یا دستورات متنی پیشرفته."""

    # اگه یکی از ویزاردهای چندمرحله‌ای فعاله، متن رو بده به همون ویزارد
    if ADMIN_WIZARD["flow"] == "campaign_create":
        handle_discount_wizard_text(chat_id, text)
        return True

    if ADMIN_WIZARD["flow"] == "search":
        reset_admin_wizard()
        show_search_results(ADMIN_CHAT_ID, text.strip())
        return True

    if ADMIN_WIZARD["flow"] == "direct_reply_chatid":
        ADMIN_WIZARD["data"]["target_chat_id"] = text.strip()
        ADMIN_WIZARD["flow"] = "direct_reply_message"
        send_message(ADMIN_CHAT_ID, "متن پیام رو بنویسید:", cancel_wizard_keyboard())
        return True

    if ADMIN_WIZARD["flow"] == "direct_reply_message":
        target = ADMIN_WIZARD["data"].get("target_chat_id")
        reset_admin_wizard()
        sent = send_message(target, f"📨 پیام از رهیار قانون:\n\n{text}")
        send_message(ADMIN_CHAT_ID, "✅ ارسال شد." if sent else "⚠️ ارسال ناموفق بود؛ آیدی چت رو چک کنید.")
        return True

    if ADMIN_WIZARD["flow"] == "consult_slot_label":
        if not text.strip():
            send_message(ADMIN_CHAT_ID, "لطفاً برچسب نوبت رو به‌صورت متن بنویسید:", cancel_wizard_keyboard())
            return True
        ADMIN_WIZARD["data"]["slot_label"] = text.strip()
        ADMIN_WIZARD["flow"] = "consult_slot_price"
        send_message(ADMIN_CHAT_ID, "💳 هزینه این نوبت رو به تومان بنویسید (فقط عدد):", cancel_wizard_keyboard())
        return True

    if ADMIN_WIZARD["flow"] == "consult_slot_price":
        price_digits = to_english_digits(text.strip())
        if not price_digits.isdigit():
            send_message(ADMIN_CHAT_ID, "لطفاً فقط عدد بفرستید (مثلاً 300000):", cancel_wizard_keyboard())
            return True
        label = ADMIN_WIZARD["data"].get("slot_label", "نوبت مشاوره")
        reset_admin_wizard()
        db.add_consultation_slot(label, format_price(int(price_digits)))
        send_message(ADMIN_CHAT_ID, f"✅ نوبت «{label}» اضافه شد.")
        return True

    if ADMIN_WIZARD["flow"] == "broadcast_content":
        if not text and not (attachment_type and attachment_file_id):
            send_message(ADMIN_CHAT_ID, "پیام خالی بود. دوباره بفرستید:", cancel_wizard_keyboard())
            return True
        ADMIN_WIZARD["data"] = {
            "text": text,
            "attachment_type": attachment_type,
            "attachment_file_id": attachment_file_id,
        }
        ADMIN_WIZARD["flow"] = "broadcast_confirm"
        count = len(db.get_all_user_chat_ids())
        preview = text or f"[{ATTACHMENT_LABELS.get(attachment_type, 'ضمیمه')}]"
        keyboard = make_keyboard([
            [("✅ ارسال به همه", "adm_broadcast_confirm")],
            [("❌ لغو", "dcw_cancel")],
        ])
        send_message(
            ADMIN_CHAT_ID,
            f"📢 پیش‌نمایش پیام:\n\n{preview}\n\nبرای {count} کاربر ارسال بشه؟",
            keyboard,
        )
        return True

    if text == "/cancel_reply":
        ADMIN_PENDING_REPLY["order_id"] = None
        ADMIN_PENDING_ASKINFO["order_id"] = None
        send_message(ADMIN_CHAT_ID, "لغو شد.")
        return True

    if text in ("/admin", "🛠 پنل مدیریت"):
        show_admin_menu(ADMIN_CHAT_ID)
        return True

    if text == "/stats":
        show_stats(ADMIN_CHAT_ID)
        return True

    if text.startswith("/find"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            prompt_admin_search(ADMIN_CHAT_ID)
        else:
            show_search_results(ADMIN_CHAT_ID, parts[1].strip())
        return True

    if text.startswith("/addcode") or text == "/discount":
        send_message(
            ADMIN_CHAT_ID,
            "🎯 ساخت کد تخفیف حالا از طریق کمپین‌هاست، دیگه نیازی به تایپ دستور نیست.\n"
            "از منوی مدیریت (/admin) روی «🎯 مدیریت کمپین‌ها» بزنید.",
        )
        return True

    if text.startswith("/addmoad"):
        # فرمت: /addmoad عنوان|تعداد روز|مرجع قانونی (مرجع اختیاری)
        body = text[len("/addmoad"):].strip()
        parts = [p.strip() for p in body.split("|")]
        if len(parts) < 2 or not parts[0] or not to_english_digits(parts[1]).isdigit():
            send_message(
                ADMIN_CHAT_ID,
                "فرمت درست:\n/addmoad عنوان|تعداد روز|مرجع قانونی (اختیاری)\n\n"
                "مثال:\n/addmoad اعتراض به رای هیات حل اختلاف کار|10|ماده ۱۵۹ قانون کار",
            )
            return True
        title, days_str = parts[0], to_english_digits(parts[1])
        legal_ref = parts[2] if len(parts) > 2 else ""
        db.add_legal_deadline(title, int(days_str), legal_ref)
        send_message(ADMIN_CHAT_ID, f"✅ موعد «{title}» ({days_str} روز) به محاسبه‌گر اضافه شد.")
        return True

    # حالت ۰: ادمین روی «درخواست اطلاعات بیشتر» زده و منتظریم سوالش رو بفرسته
    pending_askinfo_order_id = ADMIN_PENDING_ASKINFO.get("order_id")
    if pending_askinfo_order_id:
        order = db.get_order(pending_askinfo_order_id)
        ADMIN_PENDING_ASKINFO["order_id"] = None
        if not order:
            send_message(ADMIN_CHAT_ID, "⚠️ سفارش موردنظر دیگه پیدا نشد.")
            return True
        if not text:
            send_message(ADMIN_CHAT_ID, "پیام خالی بود، چیزی ارسال نشد.")
            return True
        target_chat_id = order["chat_id"]
        sent = send_message(
            target_chat_id,
            f"⏸ در مورد سفارش #{pending_askinfo_order_id} کارشناس ما نیاز به اطلاعات بیشتری داره:\n\n{text}\n\n"
            "لطفاً پاسخ خودتون رو (متن، عکس، فایل یا پیام صوتی) در همین گفتگو بفرستید.",
        )
        if sent:
            db.set_order_status(pending_askinfo_order_id, "awaiting_customer_info")
            db.log_admin_action(pending_askinfo_order_id, "awaiting_customer_info")
            db.update_user(target_chat_id, step="providing_more_info", current_order_id=pending_askinfo_order_id)
            send_message(ADMIN_CHAT_ID, "✅ درخواست اطلاعات بیشتر برای مشتری ارسال شد.")
        else:
            send_message(ADMIN_CHAT_ID, "⚠️ ارسال پیام به مشتری ناموفق بود.")
        return True

    # حالت ۱: ادمین قبلاً روی «پاسخ به این مشتری» زده و منتظریم پاسخ (متن/عکس/فایل/ویس) رو بفرسته
    pending_order_id = ADMIN_PENDING_REPLY.get("order_id")
    if pending_order_id:
        order = db.get_order(pending_order_id)
        ADMIN_PENDING_REPLY["order_id"] = None
        if not order:
            send_message(ADMIN_CHAT_ID, "⚠️ سفارش موردنظر دیگه پیدا نشد.")
            return True
        target_chat_id = order["chat_id"]

        if attachment_type and attachment_file_id:
            caption = (text or "📨 پاسخ از رهیار قانون") + f"\n\n{DISCLAIMER_TEXT}"
            sent = send_attachment(target_chat_id, attachment_type, attachment_file_id, caption)
        elif text:
            sent = send_message(target_chat_id, f"📨 پاسخ از رهیار قانون:\n\n{text}\n\n{DISCLAIMER_TEXT}")
        else:
            send_message(ADMIN_CHAT_ID, "پیام خالی بود، چیزی ارسال نشد.")
            return True

        if sent:
            db.log_admin_action(pending_order_id, "replied")
            db.save_order_final_reply(pending_order_id, text or "", attachment_type, attachment_file_id)
            db.set_order_status(pending_order_id, "completed")
            db.log_admin_action(pending_order_id, "completed")
            send_message(ADMIN_CHAT_ID, "✅ پاسخ برای مشتری ارسال شد و سفارش «تکمیل‌شده» علامت خورد.")
            rating_keyboard = make_keyboard([[
                ("۱ 😞", f"rate|{pending_order_id}|1"),
                ("۲ 🙁", f"rate|{pending_order_id}|2"),
                ("۳ 😐", f"rate|{pending_order_id}|3"),
                ("۴ 🙂", f"rate|{pending_order_id}|4"),
                ("۵ 😍", f"rate|{pending_order_id}|5"),
            ]])
            send_message(
                target_chat_id,
                "🙏 لطفاً کیفیت خدمات رو از ۱ (ضعیف) تا ۵ (عالی) امتیاز بدید:",
                rating_keyboard,
            )
        else:
            send_message(ADMIN_CHAT_ID, "⚠️ ارسال پاسخ به مشتری ناموفق بود.")
        return True

    # حالت ۲: دستور دستی — /reply <chat_id> <متن پیام>  (فقط برای متن)
    if text.startswith("/reply"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            send_message(
                ADMIN_CHAT_ID,
                "فرمت درست:\n/reply آیدی_چت متن پیام\n\nمثال:\n/reply 123456789 پاسخ سوال شما آماده است...\n\n"
                "برای ارسال عکس/فایل/ویس، از دکمه «📩 پاسخ به این مشتری» زیر پیام سفارش استفاده کنید.",
            )
            return True
        _, target_chat_id, reply_text = parts
        sent = send_message(target_chat_id, f"📨 پاسخ از رهیار قانون:\n\n{reply_text}")
        if sent:
            send_message(ADMIN_CHAT_ID, "✅ پیام ارسال شد.")
        else:
            send_message(ADMIN_CHAT_ID, "⚠️ ارسال پیام ناموفق بود. آیدی چت رو چک کنید.")
        return True

    return False


def handle_message(message):
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text") or message.get("caption") or ""
    attachment_type, attachment_file_id = extract_incoming_attachment(message)

    if any(message.get(k) for k in ("photo", "document", "voice")) and not attachment_file_id:
        logger.warning("ضمیمه دریافت شد ولی file_id پیدا نشد. ساختار پیام: %s", message)

    if not chat_id:
        return

    if chat_id != ADMIN_CHAT_ID and is_rate_limited(chat_id):
        logger.warning("محدودیت نرخ پیام برای %s فعال شد.", chat_id)
        return

    if chat_id == ADMIN_CHAT_ID and handle_admin_message(chat_id, text, attachment_type, attachment_file_id):
        return

    if text == "/myid":
        send_message(chat_id, f"آیدی چت شما: {chat_id}")
        return

    if text == "/reset":
        db.reset_user(chat_id)
        send_message(chat_id, "🔄 ثبت‌نام مجدد شروع شد!\n\nلطفاً نام خودتون رو وارد کنید:")
        return

    if text in ("/cancel", "📋 منوی اصلی"):
        if db.user_exists(chat_id):
            user = db.get_user(chat_id)
            db.update_user(chat_id, step="main_menu")
            show_main_menu(chat_id, user.get("name", ""))
        else:
            send_message(chat_id, "هنوز ثبت‌نام نکردید. برای شروع، یک پیام بفرستید.")
        return

    if text == "/deletemydata":
        if db.user_exists(chat_id):
            keyboard = make_keyboard([
                [("✅ بله، حذف کن", "confirm_delete_data")],
                [("❌ انصراف", "back_main")],
            ])
            send_message(
                chat_id,
                "⚠️ با این کار، تمام اطلاعات شما (نام، تلفن، کد ملی، تاریخچه سفارش‌ها) "
                "برای همیشه حذف می‌شه و قابل بازگشت نیست.\n\nمطمئن هستید؟",
                keyboard,
            )
        else:
            send_message(chat_id, "شما هنوز اطلاعاتی برای حذف ندارید.")
        return

    if not db.user_exists(chat_id):
        db.create_user(chat_id, step="get_name")
        send_message(
            chat_id,
            "سلام! 👋 به ربات رهیار قانون خوش اومدید ⚖️\n\n"
            "از طریق این ربات می‌تونید سوال حقوقی بپرسید، سند حقوقی سفارش بدید، "
            "یا پرونده‌تون رو پیگیری کنید — همه‌چیز همینجا و بدون نیاز به مراجعه حضوری.\n\n"
            "برای شروع، لطفاً نام خودتون رو وارد کنید:",
        )
        return

    handle_text_message(chat_id, text, attachment_type, attachment_file_id)


def handle_update(update):
    if "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        callback = update["callback_query"]
        chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        callback_data = callback.get("data", "")
        if chat_id:
            handle_callback(chat_id, callback_data)


def get_updates(offset=0):
    """لانگ‌پولینگ: تا 30 ثانیه صبر می‌کنه تا آپدیت جدید برسه، به‌جای درخواست‌های پشت‌سرهم بی‌مصرف."""
    url = f"{API_URL}/getUpdates"
    data = {"offset": offset, "timeout": 30}
    try:
        response = requests.post(url, data=data, timeout=35)
        return response.json()
    except requests.exceptions.Timeout:
        return {"ok": True, "result": []}
    except Exception as e:
        logger.exception("خطا در دریافت آپدیت: %s", e)
        return {"ok": False}


def check_stalled_orders():
    """اگه سفارشی بیش از STALLED_ORDER_HOURS ساعت در انتظار بررسی مونده، به ادمین یادآوری می‌کنه."""
    for order in db.get_stalled_orders(hours=STALLED_ORDER_HOURS):
        send_message(
            ADMIN_CHAT_ID,
            f"⏰ یادآوری: سفارش #{order['id']} ({order['order_type']}) "
            f"بیش از {STALLED_ORDER_HOURS} ساعته در انتظار بررسیه.",
        )
        db.mark_order_reminded(order["id"])


def send_daily_report():
    """خلاصه دیروز رو صبح هرروز خودکار برای ادمین می‌فرسته."""
    yesterday = (now_iran() - timedelta(days=1)).strftime("%Y-%m-%d")
    orders = db.get_orders_for_date(yesterday)
    new_users = db.get_new_users_count_for_date(yesterday)

    total = len(orders)
    confirmed = sum(1 for o in orders if o["status"] in ("confirmed", "in_progress", "completed"))
    completed = sum(1 for o in orders if o["status"] == "completed")
    revenue = sum(
        parse_price_to_int(o["price"])
        for o in orders
        if o.get("price") and o["status"] in ("confirmed", "in_progress", "completed")
    )
    send_message(
        ADMIN_CHAT_ID,
        f"📊 گزارش روزانه — {yesterday}\n\n"
        f"🆕 سفارش‌های ثبت‌شده: {total}\n"
        f"✅ پرداخت‌های تایید‌شده: {confirmed}\n"
        f"🏁 تکمیل‌شده: {completed}\n"
        f"💰 مجموع تقریبی درآمد: {format_price(revenue)} تومان\n"
        f"👥 کاربران جدید: {new_users}",
    )


def _previous_month_range(reference=None):
    """بازه [شروع، پایان) ماه میلادی قبل رو برمی‌گردونه (برای گزارش ماهانه).
    میلادی استفاده می‌شه چون کتابخونه تقویم شمسی توی محیط تست در دسترس نبود."""
    ref = reference or now_iran()
    first_of_this_month = ref.replace(day=1)
    last_day_prev_month = first_of_this_month - timedelta(days=1)
    start = last_day_prev_month.replace(day=1)
    return start.strftime("%Y-%m-%d"), first_of_this_month.strftime("%Y-%m-%d")


def send_monthly_report(chat_id=None):
    """گزارش مالی ماهانه (میلادی): سفارش‌ها، درآمد، و مجموع تخفیف کمپین‌ها به‌طور جداگانه."""
    target = chat_id or ADMIN_CHAT_ID
    start_str, end_str = _previous_month_range()
    orders = db.get_orders_between(start_str, end_str)
    new_users = db.get_new_users_count_between(start_str, end_str)

    total = len(orders)
    paid_statuses = ("confirmed", "in_progress", "completed", "awaiting_customer_info")
    revenue = sum(parse_price_to_int(o["price"]) for o in orders if o.get("price") and o["status"] in paid_statuses)
    completed = sum(1 for o in orders if o["status"] == "completed")
    total_discount = sum(o.get("discount_amount") or 0 for o in orders)

    send_message(
        target,
        f"📅 گزارش مالی ماهانه — {start_str} تا {end_str}\n\n"
        f"🆕 تعداد سفارش‌ها: {total}\n"
        f"🏁 تکمیل‌شده: {completed}\n"
        f"💰 مجموع درآمد: {format_price(revenue)} تومان\n"
        f"🎟 مجموع تخفیف داده‌شده (کمپین‌ها): {format_price(total_discount)} تومان\n"
        f"👥 کاربران جدید: {new_users}",
    )


def check_due_reminders():
    """یادآوری‌های سررسیدشده رو برای مشتری‌ها می‌فرسته و علامت اطلاع‌رسانی‌شده می‌زنه."""
    for reminder in db.get_due_reminders():
        send_message(
            reminder["chat_id"],
            f"⏰ یادآوری مهلت قانونی\n\n📌 {reminder['title']}\n📅 سررسید: {reminder['due_date']}",
        )
        db.mark_reminder_notified(reminder["id"])


def send_weekly_backup():
    """یک نسخه از فایل دیتابیس رو هفتگی برای ادمین می‌فرسته تا در صورت خرابی چیزی از دست نره."""
    caption = f"📦 پشتیبان هفتگی دیتابیس — {now_iran().strftime('%Y-%m-%d')}"
    ok = send_local_document(ADMIN_CHAT_ID, db.DB_PATH, caption=caption)
    if ok:
        logger.info("پشتیبان هفتگی دیتابیس ارسال شد.")
    else:
        logger.error("ارسال پشتیبان هفتگی ناموفق بود.")


def main():
    db.init_db()
    logger.info("ربات رهیار قانون در حال اجراست... (نسخه %s)", BOT_VERSION)
    send_message(ADMIN_CHAT_ID, f"✅ ربات رهیار قانون روشن شد.\n{get_version_info_text()}", admin_persistent_keyboard())
    offset = 0
    last_stalled_check = 0
    last_backup_time = 0
    last_report_date = None
    last_monthly_report_month = None
    last_reminder_check = 0
    while True:
        try:
            updates = get_updates(offset)
            if updates.get("ok") and updates.get("result"):
                for update in updates["result"]:
                    offset = update["update_id"] + 1
                    try:
                        handle_update(update)
                    except Exception:
                        logger.exception("خطا در پردازش آپدیت: %s", update)

            now = time.time()
            if now - last_stalled_check > STALLED_CHECK_INTERVAL_SECONDS:
                check_stalled_orders()
                last_stalled_check = now

            if now - last_reminder_check > REMINDER_CHECK_INTERVAL_SECONDS:
                check_due_reminders()
                last_reminder_check = now

            if now - last_backup_time > BACKUP_INTERVAL_SECONDS:
                send_weekly_backup()
                last_backup_time = now

            current_iran_time = now_iran()
            today_str = current_iran_time.strftime("%Y-%m-%d")
            if current_iran_time.hour == DAILY_REPORT_HOUR and last_report_date != today_str:
                send_daily_report()
                last_report_date = today_str

            month_key = current_iran_time.strftime("%Y-%m")
            if (
                current_iran_time.day == 1
                and current_iran_time.hour == DAILY_REPORT_HOUR
                and last_monthly_report_month != month_key
            ):
                send_monthly_report()
                last_monthly_report_month = month_key
        except Exception:
            logger.exception("خطای کلی در حلقه اصلی")
            time.sleep(2)


if __name__ == "__main__":
    main()
