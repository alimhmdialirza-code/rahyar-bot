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
import json
import logging

import requests
from dotenv import load_dotenv

import database as db

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

# وقتی ادمین روی دکمه «پاسخ به این مشتری» بزنه، شماره سفارش موردنظر اینجا موقتاً نگه داشته می‌شه
# تا پیام بعدی ادمین به‌جای پردازش عادی، مستقیم برای همون مشتری ارسال بشه.
ADMIN_PENDING_REPLY = {"order_id": None}

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


def forward_receipt_to_admin(kind, file_id, caption, order_id):
    """ارسال رسید پرداخت (عکس یا فایل) به ادمین همراه با دکمه تایید/رد/پاسخ."""
    keyboard = make_keyboard([
        [
            ("✅ تایید پرداخت", f"adm_confirm_{order_id}"),
            ("❌ رد پرداخت", f"adm_reject_{order_id}"),
        ],
        [("📩 پاسخ به این مشتری", f"adm_reply_{order_id}")],
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
    """دکمه «⬅️ بازگشت» که کاربر رو به مرحله قبل برمی‌گردونه.
    target: اسم مرحله‌ای که باید بهش برگرده (مثلاً main_menu, question_menu, profile, get_name...)
    cancel_order: اگه True باشه، سفارش نیمه‌کاره فعلی کاربر لغو می‌شه.
    extra_rows: ردیف‌های دکمه اضافه که قبل از دکمه بازگشت نمایش داده بشن (مثلاً دکمه «رد کردن»)."""
    rows = list(extra_rows) if extra_rows else []
    rows.append([("⬅️ بازگشت", f"goback|{target}|{1 if cancel_order else 0}")])
    return make_keyboard(rows)


def admin_persistent_keyboard():
    """یک دکمه ثابت پایین صفحه چت ادمین که همیشه دیده می‌شه (نه inline، بلکه کیبورد اصلی)."""
    return {"keyboard": [["🛠 پنل مدیریت"]], "resize_keyboard": True}


def customer_persistent_keyboard():
    """یک دکمه ثابت پایین صفحه چت مشتری برای بازگشت سریع به منوی اصلی، هر جا که باشه."""
    return {"keyboard": [["📋 منوی اصلی"]], "resize_keyboard": True}


def forward_to_admin(user, subject, detail="", order_id=None):
    text = (
        f"📨 درخواست جدید از ربات رهیار قانون\n\n"
        f"👤 نام: {user.get('name', '')} {user.get('family', '')}\n"
        f"📞 تماس: {user.get('phone', '')}\n"
        f"🪪 کد ملی: {user.get('national_id') or 'ثبت نشده'}\n"
        f"🆔 چت آیدی: {user.get('chat_id', '')}\n\n"
        f"📌 موضوع: {subject}\n{detail}"
    )
    keyboard = None
    if order_id:
        keyboard = make_keyboard([[("📩 پاسخ به این مشتری", f"adm_reply_{order_id}")]])
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
        keyboard = make_keyboard([[("📩 پاسخ به این مشتری", f"adm_reply_{order_id}")]])
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
    """کد ملی ایران باید دقیقاً ۱۰ رقم باشه."""
    cleaned = text.strip()
    return cleaned.isdigit() and len(cleaned) == 10


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
    greeting = f"{name} عزیز، " if name else ""
    keyboard = make_keyboard([
        [("❓ یک سوال حقوقی دارم", "menu_1")],
        [("📄 می‌خوام یک سند تنظیم کنم", "menu_2")],
        [("⚖️ پرونده‌ای برای پیگیری دارم", "menu_3")],
        [("💰 پیگیری پرداخت انجام‌شده", "menu_4")],
        [("📋 مشاهده قیمت‌ها و خدمات", "menu_5")],
        [("👤 حساب کاربری من", "menu_6")],
        [("📦 وضعیت سفارش‌های من", "menu_7")],
        [("⚙️ دستورات و تنظیمات", "menu_8")],
    ])
    send_message(
        chat_id,
        f"{greeting}به رهیار قانون خوش اومدید ⚖️\nلطفاً یکی از خدمات زیر رو انتخاب کنید:",
        keyboard,
    )


def handle_question_menu(chat_id):
    keyboard = make_keyboard([
        [("💬 پرسش عمومی — ۵۰,۰۰۰ تومان", "q_general")],
        [("🎓 پرسش تخصصی — ۵۰۰,۰۰۰ تومان", "q_special")],
        [("🔙 بازگشت به منوی اصلی", "back_main")],
    ])
    send_message(
        chat_id,
        "❓ چه نوع سوالی دارید؟\n\n"
        "• پرسش عمومی: پاسخ کوتاه و راهنمایی اولیه\n"
        "• پرسش تخصصی: بررسی دقیق‌تر و کارشناسی‌شده\n\n"
        "لطفاً یکی رو انتخاب کنید:",
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
        [("🔙 بازگشت به منوی اصلی", "back_main")],
    ])
    send_message(chat_id, "📄 کدوم سند رو نیاز دارید؟", keyboard)


def handle_case_menu(chat_id):
    keyboard = make_keyboard([
        [("🤝 داوری", "case_arbitration")],
        [("👨‍💼 وکالت مدنی", "case_civil")],
        [("🔙 بازگشت به منوی اصلی", "back_main")],
    ])
    send_message(
        chat_id,
        "⚖️ نوع پرونده خودتون رو انتخاب کنید\n(هزینه این خدمات به‌صورت توافقی تعیین می‌شه):",
        keyboard,
    )


def show_prices(chat_id):
    keyboard = make_keyboard([[("🔙 بازگشت به منوی اصلی", "back_main")]])
    send_message(
        chat_id,
        "📋 قیمت‌ها و خدمات رهیار قانون\n\n"
        "❓ سوال حقوقی\n"
        "• پرسش عمومی: ۵۰,۰۰۰ تومان\n"
        "• پرسش تخصصی: ۵۰۰,۰۰۰ تومان\n\n"
        "📄 تنظیم سند\n"
        "• دادخواست: ۶۹۷,۰۰۰ تومان\n"
        "• شکواییه: ۹۴۹,۰۰۰ تومان\n"
        "• اظهارنامه: ۴۹۷,۰۰۰ تومان\n"
        "• قرارداد: از ۷۹۹,۰۰۰ تومان\n"
        "• لایحه حقوقی: ۶۹۷,۰۰۰ تومان\n"
        "• لایحه کیفری: ۹۴۹,۰۰۰ تومان\n\n"
        "⚖️ پرونده (توافقی)\n"
        "• داوری\n"
        "• وکالت مدنی\n\n"
        f"📞 برای تماس مستقیم: {SUPPORT_PHONE}",
        keyboard,
    )


def show_profile(chat_id):
    user = db.get_user(chat_id)
    if not user:
        return
    text = (
        "👤 حساب کاربری من\n\n"
        f"نام: {user.get('name') or '—'}\n"
        f"نام خانوادگی: {user.get('family') or '—'}\n"
        f"شماره تماس: {user.get('phone') or '—'}\n"
        f"کد ملی: {user.get('national_id') or 'ثبت نشده'}"
    )
    keyboard = make_keyboard([
        [("✏️ ویرایش نام", "edit_name"), ("✏️ ویرایش نام خانوادگی", "edit_family")],
        [("✏️ ویرایش شماره تماس", "edit_phone"), ("✏️ ویرایش کد ملی", "edit_national_id")],
        [("🔙 بازگشت به منوی اصلی", "back_main")],
    ])
    send_message(chat_id, text, keyboard)


ORDER_STATUS_LABELS = {
    "pending_review": "⏳ در انتظار بررسی",
    "confirmed": "✅ تایید شده",
    "rejected": "❌ رد شده",
    "cancelled": "🚫 لغو شده",
}


def show_my_orders(chat_id):
    orders = db.get_orders_by_chat(chat_id)
    keyboard = make_keyboard([[("🔙 بازگشت به منوی اصلی", "back_main")]])
    if not orders:
        send_message(chat_id, "📦 شما هنوز سفارشی ثبت نکردید.", keyboard)
        return
    lines = []
    for o in orders:
        status_fa = ORDER_STATUS_LABELS.get(o["status"], o["status"])
        price_part = f" — {o['price']} تومان" if o.get("price") else ""
        lines.append(f"🆔 #{o['id']} | {o['order_type']}{price_part}\nوضعیت: {status_fa}")
    send_message(chat_id, "📦 سفارش‌های شما:\n\n" + "\n\n".join(lines), keyboard)


def show_customer_commands(chat_id):
    keyboard = make_keyboard([
        [("🔄 شروع مجدد ثبت‌نام", "cmd_reset")],
        [("⏹ لغو عملیات فعلی", "cmd_cancel")],
        [("🆔 نمایش آیدی چت من", "cmd_myid")],
        [("🗑 حذف کامل اطلاعاتم", "cmd_deletemydata")],
        [("🔙 بازگشت به منوی اصلی", "back_main")],
    ])
    send_message(chat_id, "⚙️ دستورات و تنظیمات\nیکی از گزینه‌ها رو انتخاب کنید:", keyboard)


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
        "💰 پیگیری پرداخت\n\nلطفاً تصویر رسید پرداختی که انجام دادید رو ارسال کنید تا بررسی بشه:",
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

    if callback_data == "adm_menu_search":
        prompt_admin_search(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_menu_root":
        show_admin_menu(ADMIN_CHAT_ID)
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
            "✅ پرداخت شما تایید شد!\nدرخواست شما در حال پیگیریه و به‌زودی نتیجه اطلاع داده می‌شه.",
        )
        send_message(ADMIN_CHAT_ID, f"✅ سفارش #{order_id} تایید شد.")
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
        db.set_order_rating(int(order_id_str), int(score_str))
        send_message(chat_id, "🙏 ممنون از بازخوردتون! نظرتون برامون ارزشمنده.")
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
        q_price = "۵۰,۰۰۰" if is_general else "۵۰۰,۰۰۰"
        order_id = db.create_order(chat_id, f"سوال {q_type}", price=q_price)
        db.update_user(
            chat_id,
            step="waiting_payment_question",
            question_type=q_type,
            question_price=q_price,
            current_order_id=order_id,
        )
        send_message(
            chat_id,
            payment_request_text(f"پرسش {q_type}", q_price),
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
    return make_keyboard([[("🔙 بازگشت به منوی اصلی", "back_main")]])


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


def handle_text_message(chat_id, text, attachment_type=None, attachment_file_id=None):
    user = db.get_user(chat_id)
    step = user.get("step", "main_menu")

    if step in ("get_name", "get_family", "get_phone", "get_national_id"):
        handle_registration(chat_id, text, step)

    elif step in ("editing_name", "editing_family", "editing_phone", "editing_national_id"):
        handle_profile_edit(chat_id, text, step)

    elif step == "entering_discount":
        handle_discount_entry(chat_id, text.strip())

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
        if order_id:
            db.set_order_status(order_id, "pending_review")
        forward_ok = forward_to_admin(
            user, f"پرونده {user.get('case_type', '')}", f"📝 توضیحات: {text}", order_id=order_id
        )
        db.update_user(chat_id, step="main_menu")
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
        if order_id:
            db.set_order_status(order_id, "pending_review")
        forward_ok = forward_to_admin(
            user, f"سوال {user.get('question_type', '')}", f"❓ سوال: {text}", order_id=order_id
        )
        db.update_user(chat_id, step="main_menu")
        if forward_ok:
            send_message(
                chat_id,
                "✅ سوال شما ثبت شد.\nبه‌محض بررسی، پاسخ رو براتون ارسال می‌کنیم.",
                back_to_menu_keyboard(),
            )
        else:
            send_message(
                chat_id,
                "⚠️ در ثبت سوال مشکلی پیش اومد. لطفاً دوباره تلاش کنید.",
                back_to_menu_keyboard(),
            )

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
        if order_id:
            db.set_order_status(order_id, "pending_review")
        forward_ok = forward_to_admin(
            user, f"اطلاعات سند {user.get('document_type', '')}", f"📝 اطلاعات: {text}", order_id=order_id
        )
        db.update_user(chat_id, step="main_menu")
        if forward_ok:
            send_message(
                chat_id,
                "✅ اطلاعات شما ثبت شد.\nسند شما در اسرع وقت تنظیم و ارسال می‌شه.",
                back_to_menu_keyboard(),
            )
        else:
            send_message(
                chat_id,
                "⚠️ در ثبت اطلاعات مشکلی پیش اومد. لطفاً دوباره تلاش کنید.",
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
        [("📋 سفارش‌های در انتظار بررسی", "adm_menu_pending")],
        [("📊 آمار", "adm_menu_stats")],
        [("🔍 جستجوی مشتری", "adm_menu_search")],
        [("✉️ ارسال پیام مستقیم", "adm_menu_direct_reply")],
        [("🎯 مدیریت کمپین‌ها", "adm_menu_discounts")],
        [("📖 راهنمای دستورات", "adm_menu_help")],
    ])
    send_message(chat_id, "🛠 منوی مدیریت ربات رهیار قانون\nیکی از گزینه‌ها رو انتخاب کنید:", keyboard)
    send_message(chat_id, "برای دسترسی سریع، از دکمه پایین صفحه هم می‌تونید استفاده کنید.", admin_persistent_keyboard())


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
        send_message(chat_id, text)


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
            [("📩 پاسخ به این مشتری", f"adm_reply_{order['id']}")],
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
        "🔍 جستجوی مشتری — پیدا کردن یک مشتری با اسم یا تلفن\n"
        "✉️ ارسال پیام مستقیم — پیام به یک آیدی چت خاص\n"
        "🎯 مدیریت کمپین‌ها — ساخت کمپین (دستی/خوش‌آمدگویی)، مشاهده و توقف/فعال‌سازی\n\n"
        "💡 برای پاسخ سریع به یک سفارش خاص، زیر همون سفارش دکمه «📩 پاسخ به این مشتری» هست — "
        "بزنید و بعد متن، عکس، فایل یا پیام صوتی خودتون رو بفرستید؛ مستقیم برای همون مشتری ارسال می‌شه.\n\n"
        "دستورات متنی هم هنوز کار می‌کنن اگه بخواید: /admin ، /stats ، /find، /cancel_reply",
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

    if text == "/cancel_reply":
        ADMIN_PENDING_REPLY["order_id"] = None
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
            sent = send_attachment(target_chat_id, attachment_type, attachment_file_id, text or "📨 پاسخ از رهیار قانون")
        elif text:
            sent = send_message(target_chat_id, f"📨 پاسخ از رهیار قانون:\n\n{text}")
        else:
            send_message(ADMIN_CHAT_ID, "پیام خالی بود، چیزی ارسال نشد.")
            return True

        if sent:
            db.log_admin_action(pending_order_id, "replied")
            send_message(ADMIN_CHAT_ID, "✅ پاسخ برای مشتری ارسال شد.")
            rating_keyboard = make_keyboard([[
                ("⭐️", f"rate|{pending_order_id}|1"),
                ("⭐️⭐️", f"rate|{pending_order_id}|2"),
                ("⭐️⭐️⭐️", f"rate|{pending_order_id}|3"),
                ("⭐️⭐️⭐️⭐️", f"rate|{pending_order_id}|4"),
                ("⭐️⭐️⭐️⭐️⭐️", f"rate|{pending_order_id}|5"),
            ]])
            send_message(target_chat_id, "🙏 لطفاً کیفیت خدمات رو ارزیابی کنید:", rating_keyboard)
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


STALLED_ORDER_HOURS = 6
STALLED_CHECK_INTERVAL_SECONDS = 1800  # هر ۳۰ دقیقه یک‌بار چک می‌کنه


def check_stalled_orders():
    """اگه سفارشی بیش از STALLED_ORDER_HOURS ساعت در انتظار بررسی مونده، به ادمین یادآوری می‌کنه."""
    for order in db.get_stalled_orders(hours=STALLED_ORDER_HOURS):
        send_message(
            ADMIN_CHAT_ID,
            f"⏰ یادآوری: سفارش #{order['id']} ({order['order_type']}) "
            f"بیش از {STALLED_ORDER_HOURS} ساعته در انتظار بررسیه.",
        )
        db.mark_order_reminded(order["id"])


def main():
    db.init_db()
    logger.info("ربات رهیار قانون در حال اجراست...")
    send_message(ADMIN_CHAT_ID, "✅ ربات رهیار قانون روشن شد.", admin_persistent_keyboard())
    offset = 0
    last_stalled_check = 0
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
        except Exception:
            logger.exception("خطای کلی در حلقه اصلی")
            time.sleep(2)


if __name__ == "__main__":
    main()
