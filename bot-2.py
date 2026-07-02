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


def forward_photo_to_admin_with_actions(file_id, caption, order_id):
    """ارسال رسید پرداخت به ادمین همراه با دکمه تایید/رد."""
    url = f"{API_URL}/sendPhoto"
    keyboard = make_keyboard([
        [
            ("✅ تایید پرداخت", f"adm_confirm_{order_id}"),
            ("❌ رد پرداخت", f"adm_reject_{order_id}"),
        ],
        [("📩 پاسخ به این مشتری", f"adm_reply_{order_id}")],
    ])
    data = {
        "chat_id": ADMIN_CHAT_ID,
        "photo": file_id,
        "caption": caption,
        "reply_markup": json.dumps(keyboard),
    }
    try:
        response = requests.post(url, data=data, timeout=10)
        if not response.ok:
            logger.error("sendPhoto (با دکمه) به ادمین ناموفق: %s", response.text)
            return False
        return True
    except Exception as e:
        logger.exception("خطا در ارسال عکس با دکمه به ادمین: %s", e)
        return False


def make_keyboard(buttons):
    inline_keyboard = []
    for row in buttons:
        keyboard_row = [{"text": t, "callback_data": d} for t, d in row]
        inline_keyboard.append(keyboard_row)
    return {"inline_keyboard": inline_keyboard}


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


def extract_incoming_attachment(message):
    """اگه پیام ورودی شامل عکس، فایل، یا ویس باشه، نوع و file_id اونو برمی‌گردونه."""
    if message.get("voice"):
        return "voice", message["voice"].get("file_id")
    if message.get("document"):
        return "document", message["document"].get("file_id")
    if message.get("photo"):
        photo = message["photo"]
        file_id = photo[-1].get("file_id") if isinstance(photo, list) else None
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
            send_message(chat_id, "لطفاً یک نام معتبر وارد کنید:")
            return
        db.update_user(chat_id, name=text, step="main_menu")
        send_message(chat_id, "✅ نام شما بروزرسانی شد.")

    elif step == "editing_family":
        if not text:
            send_message(chat_id, "لطفاً یک نام خانوادگی معتبر وارد کنید:")
            return
        db.update_user(chat_id, family=text, step="main_menu")
        send_message(chat_id, "✅ نام خانوادگی شما بروزرسانی شد.")

    elif step == "editing_phone":
        if not validate_phone(text):
            send_message(chat_id, "⚠️ شماره تماس معتبر نیست. لطفاً فقط رقم وارد کنید:")
            return
        db.update_user(chat_id, phone=text, step="main_menu")
        send_message(chat_id, "✅ شماره تماس شما بروزرسانی شد.")

    elif step == "editing_national_id":
        if not validate_national_id(text):
            send_message(chat_id, "⚠️ کد ملی باید دقیقاً ۱۰ رقم باشه. دوباره وارد کنید:")
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
        send_message(chat_id, "عالی! حالا نام خانوادگی خودتون رو وارد کنید:")

    elif step == "get_family":
        if not text.strip():
            send_message(chat_id, "لطفاً نام خانوادگی خودتون رو وارد کنید:")
            return
        db.update_user(chat_id, family=text.strip(), step="get_phone")
        send_message(chat_id, "📞 شماره تماس خودتون رو وارد کنید (مثال: 09121234567):")

    elif step == "get_phone":
        if not validate_phone(text):
            send_message(
                chat_id,
                "⚠️ شماره تماس واردشده معتبر نیست.\nلطفاً فقط رقم و بدون فاصله وارد کنید (مثال: 09121234567):",
            )
            return
        db.update_user(chat_id, phone=text.strip(), step="get_national_id")
        keyboard = make_keyboard([[("رد کردن (اختیاری)", "skip_national_id")]])
        send_message(chat_id, "🪪 کد ملی خودتون رو وارد کنید:\n(وارد کردن این مورد اختیاریه)", keyboard)

    elif step == "get_national_id":
        if not validate_national_id(text):
            send_message(
                chat_id,
                "⚠️ کد ملی باید دقیقاً ۱۰ رقم باشه.\nلطفاً دوباره وارد کنید یا از دکمه «رد کردن» استفاده کنید:",
                make_keyboard([[("رد کردن (اختیاری)", "skip_national_id")]]),
            )
            return
        finish_registration(chat_id, text.strip())


def finish_registration(chat_id, national_id):
    db.update_user(chat_id, national_id=national_id, step="main_menu")
    user = db.get_user(chat_id)
    send_message(chat_id, "✅ ثبت‌نام شما با موفقیت انجام شد!")
    show_main_menu(chat_id, user.get("name", ""))


# ---------------------------------------------------------------------------
# مدیریت دکمه‌ها (کالبک‌ها)
# ---------------------------------------------------------------------------

def handle_admin_callback(callback_data):
    """دکمه‌های تایید/رد پرداخت، پاسخ به مشتری، و منوی مدیریت که فقط ادمین می‌بینه."""
    if callback_data == "adm_menu_pending":
        show_pending_orders(ADMIN_CHAT_ID)
        return

    if callback_data == "adm_menu_help":
        show_admin_help(ADMIN_CHAT_ID)
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
        send_message(
            user_chat_id,
            "✅ پرداخت شما تایید شد!\nدرخواست شما در حال پیگیریه و به‌زودی نتیجه اطلاع داده می‌شه.",
        )
        send_message(ADMIN_CHAT_ID, f"✅ سفارش #{order_id} تایید شد.")
    else:
        db.set_order_status(order_id, "rejected")
        send_message(
            user_chat_id,
            "❌ متاسفانه رسید پرداخت شما تایید نشد.\n"
            "لطفاً از صحت واریزی مطمئن بشید و دوباره تصویر رسید رو ارسال کنید، "
            f"یا برای پیگیری با {SUPPORT_PHONE} تماس بگیرید.",
        )
        send_message(ADMIN_CHAT_ID, f"❌ سفارش #{order_id} رد شد.")


def handle_callback(chat_id, callback_data):
    if chat_id == ADMIN_CHAT_ID and callback_data.startswith("adm_"):
        handle_admin_callback(callback_data)
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

    elif callback_data in PROFILE_FIELD_PROMPTS:
        new_step, prompt = PROFILE_FIELD_PROMPTS[callback_data]
        db.update_user(chat_id, step=new_step)
        send_message(chat_id, prompt)

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
        send_message(chat_id, payment_request_text(f"پرسش {q_type}", q_price))

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
        send_message(chat_id, payment_request_text(doc_name, doc_price))

    elif callback_data in ("case_arbitration", "case_civil"):
        case_type = "داوری" if callback_data == "case_arbitration" else "وکالت مدنی"
        order_id = db.create_order(chat_id, f"پرونده {case_type}")
        db.update_user(chat_id, step="case_detail", case_type=case_type, current_order_id=order_id)
        send_message(
            chat_id,
            f"⚖️ پرونده {case_type}\n\nلطفاً توضیح مختصری از موضوع پرونده‌تون بنویسید:",
        )


# ---------------------------------------------------------------------------
# مدیریت پیام‌های متنی و عکس
# ---------------------------------------------------------------------------

def back_to_menu_keyboard():
    return make_keyboard([[("🔙 بازگشت به منوی اصلی", "back_main")]])


def handle_text_message(chat_id, text, attachment_type=None, attachment_file_id=None):
    user = db.get_user(chat_id)
    step = user.get("step", "main_menu")

    if step in ("get_name", "get_family", "get_phone", "get_national_id"):
        handle_registration(chat_id, text, step)

    elif step in ("editing_name", "editing_family", "editing_phone", "editing_national_id"):
        handle_profile_edit(chat_id, text, step)

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
                )
                return
        if not text:
            send_message(chat_id, "لطفاً توضیح مختصری از موضوع پرونده‌تون بنویسید:")
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
        if attachment_type != "photo" or not attachment_file_id:
            send_message(chat_id, "📎 لطفاً فقط تصویر رسید پرداخت رو ارسال کنید:")
            return
        order_id = user.get("current_order_id")
        caption = f"رسید پرداخت — سوال {user.get('question_type', '')} — {user.get('question_price', '')} تومان"
        sent = forward_photo_to_admin_with_actions(attachment_file_id, caption, order_id)
        if sent:
            db.attach_receipt(order_id, attachment_file_id)
            db.update_user(chat_id, step="asking_question")
            send_message(
                chat_id,
                "✅ رسید دریافت شد!\n\nحالا سوال حقوقی خودتون رو بنویسید. "
                "می‌تونید مدارک مرتبط (عکس، فایل، پیام صوتی) رو هم همراه یا جدا ارسال کنید:",
            )
        else:
            send_message(
                chat_id,
                "⚠️ ارسال رسید با مشکل مواجه شد. لطفاً چند لحظه دیگه دوباره تصویر رسید رو ارسال کنید.",
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
                )
                return
        if not text:
            send_message(chat_id, "لطفاً متن سوال حقوقی خودتون رو بنویسید:")
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
        if attachment_type != "photo" or not attachment_file_id:
            send_message(chat_id, "📎 لطفاً فقط تصویر رسید پرداخت رو ارسال کنید:")
            return
        order_id = user.get("current_order_id")
        caption = f"رسید پرداخت — {user.get('document_type', '')} — {user.get('document_price', '')} تومان"
        sent = forward_photo_to_admin_with_actions(attachment_file_id, caption, order_id)
        if sent:
            db.attach_receipt(order_id, attachment_file_id)
            db.update_user(chat_id, step="document_detail")
            send_message(
                chat_id,
                "✅ رسید دریافت شد!\n\nحالا اطلاعات لازم برای تنظیم سند رو بنویسید. "
                "می‌تونید مدارک مرتبط (عکس، فایل، پیام صوتی) رو هم ارسال کنید:",
            )
        else:
            send_message(
                chat_id,
                "⚠️ ارسال رسید با مشکل مواجه شد. لطفاً چند لحظه دیگه دوباره تصویر رسید رو ارسال کنید.",
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
                )
                return
        if not text:
            send_message(chat_id, "لطفاً اطلاعات لازم برای تنظیم سند رو بنویسید:")
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
        if attachment_type != "photo" or not attachment_file_id:
            send_message(chat_id, "📎 لطفاً فقط تصویر رسید پرداخت رو ارسال کنید:")
            return
        order_id = user.get("current_order_id")
        sent = forward_photo_to_admin_with_actions(attachment_file_id, "رسید پیگیری پرداخت", order_id)
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
        [("📖 راهنمای دستورات", "adm_menu_help")],
    ])
    send_message(chat_id, "🛠 منوی مدیریت ربات رهیار قانون\nیکی از گزینه‌ها رو انتخاب کنید:", keyboard)


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


def show_admin_help(chat_id):
    send_message(
        chat_id,
        "📖 راهنمای دستورات مدیریت\n\n"
        "/admin — نمایش منوی مدیریت (لیست سفارش‌ها و راهنما)\n"
        "/reply آیدی_چت متن — ارسال پیام مستقیم به یک مشتری خاص\n"
        "/cancel_reply — لغو حالت «در انتظار پاسخ»\n\n"
        "💡 برای پاسخ سریع‌تر، زیر هر سفارش دکمه «📩 پاسخ به این مشتری» هست — "
        "کافیه بزنید و بعد متن، عکس، فایل یا پیام صوتی خودتون رو بفرستید؛ "
        "مستقیم برای همون مشتری ارسال می‌شه.",
    )


# ---------------------------------------------------------------------------
# ورودی اصلی پیام‌ها
# ---------------------------------------------------------------------------

def handle_admin_message(chat_id, text, attachment_type=None, attachment_file_id=None):
    """پیام‌های ادمین: حالت پاسخ به مشتری (متن/عکس/فایل/ویس)، منوی مدیریت، یا دستور دستی /reply."""
    if text == "/cancel_reply":
        ADMIN_PENDING_REPLY["order_id"] = None
        send_message(ADMIN_CHAT_ID, "لغو شد.")
        return True

    if text == "/admin":
        show_admin_menu(ADMIN_CHAT_ID)
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
            send_message(ADMIN_CHAT_ID, "✅ پاسخ برای مشتری ارسال شد.")
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

    if not chat_id:
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

    if text == "/cancel":
        if db.user_exists(chat_id):
            user = db.get_user(chat_id)
            db.update_user(chat_id, step="main_menu")
            send_message(chat_id, "عملیات لغو شد.")
            show_main_menu(chat_id, user.get("name", ""))
        else:
            send_message(chat_id, "هنوز ثبت‌نام نکردید. برای شروع، یک پیام بفرستید.")
        return

    if not db.user_exists(chat_id):
        db.create_user(chat_id, step="get_name")
        send_message(
            chat_id,
            "سلام! 👋\nبه ربات رهیار قانون خوش اومدید ⚖️\n\nبرای شروع لطفاً نام خودتون رو وارد کنید:",
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


def main():
    db.init_db()
    logger.info("ربات رهیار قانون در حال اجراست...")
    offset = 0
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
        except Exception:
            logger.exception("خطای کلی در حلقه اصلی")
            time.sleep(2)


if __name__ == "__main__":
    main()
