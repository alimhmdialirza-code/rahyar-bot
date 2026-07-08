"""
مجموعه تست‌های خودکار ربات رهیار قانون.

این‌ها همون سناریوهایی هستن که در طول توسعه ربات، هر بار دستی نوشته و اجرا
می‌شدن تا مطمئن بشیم یک تغییر جدید چیز قدیمی رو خراب نکرده. حالا برای همیشه
اینجا نگه داشته می‌شن تا هر وقت خواستید (حتی ماه‌ها بعد) بتونید با یک دستور
ساده کل ربات رو تست کنید.

نحوه اجرا:
    pip install pytest
    pytest tests/

اگه همه‌چیز سبز (PASSED) بود، یعنی قابلیت‌های اصلی ربات درست کار می‌کنن.
اگه یک تست قرمز (FAILED) شد، دقیقاً همون بخش رو بهتون نشون می‌ده که باید
بررسی کنید — قبل از این‌که کاربر واقعی به مشکل بخوره.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# این متغیرها باید قبل از import کردن bot تنظیم بشن، چون bot.py موقع import
# چک می‌کنه که BOT_TOKEN و ADMIN_CHAT_ID موجود باشن.
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_CHAT_ID", "999999")
os.environ.setdefault("CARD_NUMBER", "6221061237894102")
os.environ.setdefault("SUPPORT_PHONE", "09931012756")
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")

import requests  # noqa: E402

SENT_LOG = []


class _FakeResponse:
    ok = True
    text = "ok"

    def json(self):
        return {"ok": True, "result": []}


def _fake_post(url, data=None, files=None, timeout=None):
    """به‌جای ارسال واقعی به بله، فقط پیام رو ثبت می‌کنه تا بتونیم بررسیش کنیم."""
    SENT_LOG.append((url, data.get("chat_id") if data else None, data.get("text") if data else None, data))
    return _FakeResponse()


requests.post = _fake_post

import bot  # noqa: E402
import database as db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """قبل از هر تست، دیتابیس رو خالی می‌کنه تا تست‌ها روی هم اثر نذارن."""
    db.init_db()
    SENT_LOG.clear()
    yield
    with db.get_connection() as conn:
        for table in ("users", "orders", "admin_actions", "discount_codes", "campaigns", "campaign_redemptions"):
            conn.execute(f"DELETE FROM {table}")


def register(cid, nid="0499370899"):
    """یک کاربر تستی رو کامل ثبت‌نام می‌کنه (کد ملی پیش‌فرض از نظر فرمولی معتبره)."""
    bot.handle_message({"chat": {"id": cid}, "text": "سلام"})
    bot.handle_message({"chat": {"id": cid}, "text": "نام"})
    bot.handle_message({"chat": {"id": cid}, "text": "فامیل"})
    bot.handle_message({"chat": {"id": cid}, "text": "09121112233"})
    bot.handle_message({"chat": {"id": cid}, "text": nid})


def cb(cid, data):
    """شبیه‌سازی زدن یک دکمه inline توسط کاربر."""
    bot.handle_update({"callback_query": {"message": {"chat": {"id": cid}}, "data": data}})


# ---------------------------------------------------------------------------
# ثبت‌نام
# ---------------------------------------------------------------------------

def test_registration_completes():
    register(101)
    u = db.get_user("101")
    assert u["step"] == "main_menu"
    assert u["name"] == "نام"


def test_invalid_national_id_is_rejected():
    bot.handle_message({"chat": {"id": 102}, "text": "سلام"})
    bot.handle_message({"chat": {"id": 102}, "text": "نام"})
    bot.handle_message({"chat": {"id": 102}, "text": "فامیل"})
    bot.handle_message({"chat": {"id": 102}, "text": "09121112233"})
    bot.handle_message({"chat": {"id": 102}, "text": "1234567890"})  # چک‌دیجیت نامعتبر
    u = db.get_user("102")
    assert u["step"] == "get_national_id"


# ---------------------------------------------------------------------------
# جریان سوال حقوقی + مرور نهایی قبل از ارسال
# ---------------------------------------------------------------------------

def test_question_flow_with_review_step():
    register(103)
    cb(103, "menu_1")
    cb(103, "q_general")
    bot.handle_message({"chat": {"id": 103}, "photo": [{"file_id": "R1"}]})
    assert db.get_user("103")["step"] == "asking_question"

    bot.handle_message({"chat": {"id": 103}, "text": "سوال تستی"})
    assert db.get_user("103")["step"] == "asking_question_review"
    # قبل از تایید، هنوز نباید به ادمین رسیده باشه
    assert not any(cid == "999999" and txt and "سوال تستی" in (txt or "") for _, cid, txt, _ in SENT_LOG)

    cb(103, "review_confirm_asking_question")
    assert db.get_user("103")["step"] == "main_menu"
    order = db.get_orders_by_chat("103")[0]
    assert order["status"] == "pending_review"


def test_review_edit_lets_user_retype():
    register(109)
    cb(109, "menu_1")
    cb(109, "q_general")
    bot.handle_message({"chat": {"id": 109}, "photo": [{"file_id": "R1"}]})
    bot.handle_message({"chat": {"id": 109}, "text": "متن با غلط"})
    cb(109, "review_edit_asking_question")
    assert db.get_user("109")["step"] == "asking_question"
    bot.handle_message({"chat": {"id": 109}, "text": "متن درست"})
    cb(109, "review_confirm_asking_question")
    admin_text = next(txt for _, cid, txt, _ in SENT_LOG if cid == "999999" and txt and "متن درست" in txt)
    assert "متن با غلط" not in admin_text


# ---------------------------------------------------------------------------
# کد تخفیف و کمپین
# ---------------------------------------------------------------------------

def test_discount_code_applies_correct_amount():
    campaign_id = db.create_campaign("تست", "manual", 20, 5, 1, auto_refill=False)
    code = db.list_active_discount_codes(campaign_id=campaign_id)[0]["code"]

    register(104)
    cb(104, "menu_1")
    cb(104, "q_special")  # ۵۰۰,۰۰۰
    order_id = db.get_user("104")["current_order_id"]
    cb(104, f"enter_discount_{order_id}")
    bot.handle_message({"chat": {"id": 104}, "text": code})
    order = db.get_order(order_id)
    assert bot.parse_price_to_int(order["price"]) == 400000


def test_welcome_campaign_gift_not_granted_twice():
    db.create_campaign("خوش‌آمد", "welcome", 10, 1, 5, auto_refill=True)
    register(106)
    assert any(cid == "106" and txt and "هدیه خوش‌آمدگویی" in (txt or "") for _, cid, txt, _ in SENT_LOG)

    db.delete_user_completely("106")
    SENT_LOG.clear()
    register(106)
    assert not any(cid == "106" and txt and "هدیه خوش‌آمدگویی" in (txt or "") for _, cid, txt, _ in SENT_LOG)


# ---------------------------------------------------------------------------
# مسیر کامل سفارش نزد ادمین (تایید → در حال آماده‌سازی → تکمیل + آرشیو)
# ---------------------------------------------------------------------------

def test_admin_order_pipeline_and_reply_archive():
    register(105)
    cb(105, "menu_1")
    cb(105, "q_general")
    bot.handle_message({"chat": {"id": 105}, "photo": [{"file_id": "R1"}]})
    order_id = db.get_user("105")["current_order_id"]
    bot.handle_message({"chat": {"id": 105}, "text": "سوال"})
    cb(105, "review_confirm_asking_question")

    cb(999999, f"adm_confirm_{order_id}")
    assert db.get_order(order_id)["status"] == "confirmed"

    cb(999999, f"adm_start_{order_id}")
    assert db.get_order(order_id)["status"] == "in_progress"

    cb(999999, f"adm_reply_{order_id}")
    bot.handle_message({"chat": {"id": 999999}, "text": "پاسخ نهایی"})
    order = db.get_order(order_id)
    assert order["status"] == "completed"
    assert order["final_reply_text"] == "پاسخ نهایی"


# ---------------------------------------------------------------------------
# پیام همگانی
# ---------------------------------------------------------------------------

def test_broadcast_reaches_all_registered_users():
    register(107)
    register(108)
    SENT_LOG.clear()
    cb(999999, "adm_menu_broadcast")
    bot.handle_message({"chat": {"id": 999999}, "text": "پیام همگانی تستی"})
    cb(999999, "adm_broadcast_confirm")
    received = [cid for _, cid, txt, _ in SENT_LOG if txt == "پیام همگانی تستی"]
    assert "107" in received
    assert "108" in received


# ---------------------------------------------------------------------------
# گزارش‌ها
# ---------------------------------------------------------------------------

def test_daily_report_does_not_crash():
    bot.send_daily_report()
    assert any(cid == "999999" and txt and "گزارش روزانه" in (txt or "") for _, cid, txt, _ in SENT_LOG)


def test_stats_command_runs():
    bot.handle_message({"chat": {"id": 999999}, "text": "/stats"})
    assert any(cid == "999999" and txt and "آمار" in (txt or "") for _, cid, txt, _ in SENT_LOG)
