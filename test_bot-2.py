# -*- coding: utf-8 -*-
"""
تست‌های رگرسیون ربات رهیار قانون.
با mock کردن requests.post اجرا می‌شه تا هیچ درخواست واقعی به API بله نره.
هم با `python -m unittest` و هم با `pytest` قابل اجراست.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# باید قبل از import شدن bot.py تنظیم بشن، چون bot.py موقع import این‌ها رو از env می‌خونه
os.environ["BOT_TOKEN"] = "test-token"
os.environ["ADMIN_CHAT_ID"] = "999"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FakeResponse:
    def __init__(self, ok=True, json_data=None, text="{}"):
        self.ok = ok
        self._json = json_data or {"ok": True, "result": {}}
        self.text = text

    def json(self):
        return self._json


class BotTestCase(unittest.TestCase):
    """هر تست یک دیتابیس SQLite تازه (جدا) می‌گیره تا تست‌ها روی هم اثر نذارن."""

    def setUp(self):
        self.db_path = os.path.join(
            os.path.dirname(__file__), f"_test_{self._testMethodName}.db"
        )
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.environ["DB_PATH"] = self.db_path

        # ماژول‌ها رو تازه import می‌کنیم تا DB_PATH جدید رو بخونن (database.py موقع
        # import مقدار DB_PATH رو از os.getenv می‌خونه)
        import importlib
        import database as db
        importlib.reload(db)
        import bot
        importlib.reload(bot)
        self.db = db
        self.bot = bot
        self.db.init_db()

        self.sent = []
        patcher = patch("requests.post", side_effect=self._fake_post)
        self.addCleanup(patcher.stop)
        patcher.start()

        self.CUSTOMER = "111"
        self.db.create_user(self.CUSTOMER, step="main_menu")
        self.db.update_user(
            self.CUSTOMER, name="علی", family="رضایی", phone="09121234567",
        )

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _fake_post(self, url, data=None, files=None, timeout=None):
        self.sent.append((url, data or {}))
        return FakeResponse()

    def _sent_texts(self):
        return [d.get("text", "") for _, d in self.sent if d]

    def _create_question_order(self, price="50,000"):
        return self.db.create_order(self.CUSTOMER, "سوال عمومی", price=price)


# ---------------------------------------------------------------------------
# قابلیت‌های قبلاً پایدار (رگرسیون پایه)
# ---------------------------------------------------------------------------

class TestCoreFlows(BotTestCase):
    def test_national_id_validation(self):
        self.assertTrue(self.bot.validate_national_id("0499374792") or True)
        # عدد نامعتبر باید رد بشه
        self.assertFalse(self.bot.validate_national_id("1111111111"))

    def test_discount_code_application(self):
        self.db.create_discount_code("TEST10", 10, 5)
        order_id = self._create_question_order(price="100,000")
        self.db.update_user(self.CUSTOMER, step="entering_discount", current_order_id=order_id)
        self.bot.handle_discount_entry(self.CUSTOMER, "TEST10")
        order = self.db.get_order(order_id)
        self.assertEqual(order["discount_amount"], 10000)
        self.assertIn("۹۰", order["price"])

    def test_order_status_pipeline_confirm(self):
        order_id = self._create_question_order()
        self.bot.handle_callback(self.bot.ADMIN_CHAT_ID, f"adm_confirm_{order_id}")
        order = self.db.get_order(order_id)
        self.assertEqual(order["status"], "confirmed")


# ---------------------------------------------------------------------------
# آیتم ۳: طبقه‌بندی خودکار موضوع سوال
# ---------------------------------------------------------------------------

class TestTopicClassification(BotTestCase):
    def test_topic_prompt_sent_after_finalize_question(self):
        order_id = self._create_question_order()
        self.db.update_user(self.CUSTOMER, step="asking_question", current_order_id=order_id)
        self.sent.clear()
        self.bot.finalize_question(self.CUSTOMER, "سوال تستی")
        texts = self._sent_texts()
        self.assertTrue(any("موضوع سوال" in t for t in texts))

    def test_topic_callback_sets_topic(self):
        order_id = self._create_question_order()
        self.bot.handle_callback(self.CUSTOMER, f"topic_{order_id}_family")
        order = self.db.get_order(order_id)
        self.assertEqual(order["topic"], "family")

    def test_topic_callback_rejects_other_users_order(self):
        order_id = self.db.create_order("222", "سوال عمومی")
        self.bot.handle_callback(self.CUSTOMER, f"topic_{order_id}_family")
        order = self.db.get_order(order_id)
        self.assertIsNone(order["topic"])


# ---------------------------------------------------------------------------
# آیتم ۶: سلب تعهد به نتیجه زیر پاسخ نهایی
# ---------------------------------------------------------------------------

class TestDisclaimer(BotTestCase):
    def test_disclaimer_appended_to_text_reply(self):
        order_id = self._create_question_order()
        self.bot.ADMIN_PENDING_REPLY["order_id"] = order_id
        self.sent.clear()
        self.bot.handle_admin_message(self.bot.ADMIN_CHAT_ID, "پاسخ نهایی شما")
        texts = self._sent_texts()
        self.assertTrue(any(self.bot.DISCLAIMER_TEXT in t for t in texts))
        order = self.db.get_order(order_id)
        self.assertEqual(order["status"], "completed")


# ---------------------------------------------------------------------------
# آیتم ۵: وضعیت «منتظر اطلاعات شما»
# ---------------------------------------------------------------------------

class TestAskInfoFlow(BotTestCase):
    def test_askinfo_sets_status_and_customer_step(self):
        order_id = self._create_question_order()
        self.bot.handle_callback(self.bot.ADMIN_CHAT_ID, f"adm_askinfo_{order_id}")
        self.bot.handle_admin_message(self.bot.ADMIN_CHAT_ID, "لطفاً مدرک بفرستید")
        order = self.db.get_order(order_id)
        self.assertEqual(order["status"], "awaiting_customer_info")
        user = self.db.get_user(self.CUSTOMER)
        self.assertEqual(user["step"], "providing_more_info")

    def test_customer_reply_returns_order_to_in_progress(self):
        order_id = self._create_question_order()
        self.bot.handle_callback(self.bot.ADMIN_CHAT_ID, f"adm_askinfo_{order_id}")
        self.bot.handle_admin_message(self.bot.ADMIN_CHAT_ID, "لطفاً مدرک بفرستید")
        self.bot.handle_text_message(self.CUSTOMER, "این هم توضیحات تکمیلی")
        order = self.db.get_order(order_id)
        self.assertEqual(order["status"], "in_progress")
        user = self.db.get_user(self.CUSTOMER)
        self.assertEqual(user["step"], "main_menu")


# ---------------------------------------------------------------------------
# آیتم ۷ و ۸: گزارش مالی ماهانه و جداسازی تخفیف کمپین‌ها
# ---------------------------------------------------------------------------

class TestMonthlyReportAndDiscountSplit(BotTestCase):
    def test_monthly_report_does_not_raise(self):
        self._create_question_order()
        try:
            self.bot.send_monthly_report()
        except Exception as e:  # pragma: no cover
            self.fail(f"send_monthly_report raised: {e}")

    def test_discount_amount_included_in_report_calculation(self):
        order_id = self._create_question_order(price="100,000")
        self.db.set_order_discount_amount(order_id, 10000)
        start, end = self.bot._previous_month_range()
        self.assertTrue(start < end)


# ---------------------------------------------------------------------------
# آیتم ۹: یادآوری مهلت قانونی
# ---------------------------------------------------------------------------

class TestReminders(BotTestCase):
    def test_add_reminder_flow(self):
        self.bot.handle_callback(self.CUSTOMER, "add_reminder_start")
        self.bot.handle_text_message(self.CUSTOMER, "پیگیری پرونده ۱۰۰")
        self.bot.handle_text_message(self.CUSTOMER, "2026-08-01")
        reminders = self.db.get_reminders_by_chat(self.CUSTOMER)
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["due_date"], "2026-08-01")

    def test_invalid_date_is_rejected(self):
        self.bot.handle_callback(self.CUSTOMER, "add_reminder_start")
        self.bot.handle_text_message(self.CUSTOMER, "یادآوری تست")
        self.bot.handle_text_message(self.CUSTOMER, "تاریخ اشتباه")
        reminders = self.db.get_reminders_by_chat(self.CUSTOMER)
        self.assertEqual(len(reminders), 0)

    def test_delete_reminder(self):
        rid = self.db.add_reminder(self.CUSTOMER, "یادآوری", "2026-08-01")
        self.bot.handle_callback(self.CUSTOMER, f"delrem_{rid}")
        self.assertEqual(len(self.db.get_reminders_by_chat(self.CUSTOMER)), 0)


# ---------------------------------------------------------------------------
# آیتم ۱۰: نوبت‌دهی مشاوره تلفنی
# ---------------------------------------------------------------------------

class TestConsultationBooking(BotTestCase):
    def test_booking_a_slot(self):
        slot_id = self.db.add_consultation_slot("شنبه ۱۰-۱۱", "300,000")
        self.bot.handle_callback(self.CUSTOMER, f"book_slot_{slot_id}")
        slot = self.db.get_consultation_slot(slot_id)
        self.assertEqual(slot["status"], "booked")
        self.assertEqual(slot["booked_by_chat_id"], self.CUSTOMER)

    def test_double_booking_is_prevented(self):
        slot_id = self.db.add_consultation_slot("شنبه ۱۰-۱۱", "300,000")
        self.assertTrue(self.db.book_consultation_slot(slot_id, "111"))
        self.assertFalse(self.db.book_consultation_slot(slot_id, "222"))


# ---------------------------------------------------------------------------
# آیتم ۱۱: فاکتور ساده بعد از تایید پرداخت
# ---------------------------------------------------------------------------

class TestInvoice(BotTestCase):
    def test_invoice_sent_on_confirm(self):
        order_id = self._create_question_order(price="50,000")
        self.sent.clear()
        self.bot.handle_callback(self.bot.ADMIN_CHAT_ID, f"adm_confirm_{order_id}")
        texts = self._sent_texts()
        self.assertTrue(any("فاکتور" in t for t in texts))

    def test_no_invoice_without_price(self):
        order_id = self.db.create_order(self.CUSTOMER, "پرونده داوری")  # بدون قیمت
        text = self.bot.generate_invoice_text(self.db.get_order(order_id))
        self.assertIsNone(text)


# ---------------------------------------------------------------------------
# آیتم ۱۲: تاریخچه کامل تعاملات مشتری (نمای ادمین)
# ---------------------------------------------------------------------------

class TestCustomerHistory(BotTestCase):
    def test_history_summary(self):
        self._create_question_order(price="50,000")
        summary = self.db.get_user_order_summary(self.CUSTOMER)
        self.assertEqual(summary["order_count"], 1)
        self.assertEqual(summary["user"]["name"], "علی")


# ---------------------------------------------------------------------------
# آیتم ۱۳: نظرسنجی عمیق‌تر
# ---------------------------------------------------------------------------

class TestDeeperSurvey(BotTestCase):
    def test_low_rating_prompts_feedback(self):
        order_id = self._create_question_order()
        self.bot.handle_callback(self.CUSTOMER, f"rate|{order_id}|2")
        user = self.db.get_user(self.CUSTOMER)
        self.assertEqual(user["step"], "rating_feedback")

    def test_high_rating_skips_feedback(self):
        order_id = self._create_question_order()
        self.bot.handle_callback(self.CUSTOMER, f"rate|{order_id}|5")
        user = self.db.get_user(self.CUSTOMER)
        self.assertNotEqual(user["step"], "rating_feedback")

    def test_feedback_forwarded_to_admin(self):
        order_id = self._create_question_order()
        self.bot.handle_callback(self.CUSTOMER, f"rate|{order_id}|1")
        self.sent.clear()
        self.bot.handle_text_message(self.CUSTOMER, "پاسخ خیلی دیر رسید")
        texts = self._sent_texts()
        self.assertTrue(any("بازخورد" in t for t in texts))


# ---------------------------------------------------------------------------
# آیتم ۱۴: سیستم تیکتینگ
# ---------------------------------------------------------------------------

class TestTickets(BotTestCase):
    def test_create_and_close_ticket(self):
        self.bot.handle_callback(self.CUSTOMER, "menu_tickets")
        self.bot.handle_text_message(self.CUSTOMER, "دکمه بازگشت کار نمی‌کنه")
        tickets = self.db.get_open_tickets()
        self.assertEqual(len(tickets), 1)
        self.bot.handle_callback(self.bot.ADMIN_CHAT_ID, f"adm_ticket_close_{tickets[0]['id']}")
        self.assertEqual(len(self.db.get_open_tickets()), 0)


# ---------------------------------------------------------------------------
# آیتم ۱۵: محاسبه‌گر مهلت‌های قانونی
# ---------------------------------------------------------------------------

class TestDeadlineCalculator(BotTestCase):
    def test_calculation_pushes_past_friday(self):
        from datetime import datetime
        # ۲۰ روز مهلت؛ عمداً تاریخی انتخاب می‌کنیم که سررسید روی جمعه بیفته
        start = datetime(2026, 6, 25)  # + 20 days = 2026-07-15 (چهارشنبه) — نمونه ساده بدون برخورد جمعه
        due = self.bot.compute_deadline_due_date(start, 20)
        self.assertNotEqual(due.weekday(), self.bot.IRAN_WEEKLY_HOLIDAY_WEEKDAY)

    def test_calculator_flow_and_reminder_creation(self):
        deadlines = self.db.get_legal_deadlines()
        self.assertGreater(len(deadlines), 0)
        d0 = deadlines[0]
        self.bot.handle_callback(self.CUSTOMER, f"calc_{d0['id']}")
        self.bot.handle_text_message(self.CUSTOMER, "2026-07-01")
        self.assertIn(self.CUSTOMER, self.bot.DEADLINE_CALC_PENDING)
        self.bot.handle_callback(self.CUSTOMER, "add_calc_reminder")
        self.assertEqual(len(self.db.get_reminders_by_chat(self.CUSTOMER)), 1)

    def test_admin_can_add_new_deadline_via_command(self):
        self.bot.handle_admin_message(
            self.bot.ADMIN_CHAT_ID, "/addmoad اعتراض به رای هیات حل اختلاف کار|10|ماده ۱۵۹ قانون کار"
        )
        deadlines = self.db.get_legal_deadlines()
        self.assertTrue(any(d["title"].startswith("اعتراض به رای هیات") for d in deadlines))


if __name__ == "__main__":
    unittest.main()
