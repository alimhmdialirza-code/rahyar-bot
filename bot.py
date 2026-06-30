import requests
import time
import json

TOKEN = "741504271:glrJAftdMTf2g10n8GCGAN2iFPeveKDehc8"
API_URL = f"https://tapi.bale.ai/bot{TOKEN}"
ADMIN_CHAT_ID = "866843345"
CARD_NUMBER = "6221061237894102"

users = {}


def send_message(chat_id, text, keyboard=None):
    url = f"{API_URL}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"خطا در ارسال پیام: {e}")


def make_keyboard(buttons):
    inline_keyboard = []
    for row in buttons:
        keyboard_row = []
        for btn_text, btn_data in row:
            keyboard_row.append({"text": btn_text, "callback_data": btn_data})
        inline_keyboard.append(keyboard_row)
    return {"inline_keyboard": inline_keyboard}


def forward_to_admin(user_data, subject, detail=""):
    text = f"""📨 درخواست جدید از ربات رهیار قانون

👤 نام: {user_data.get('name', '')} {user_data.get('family', '')}
📞 تماس: {user_data.get('phone', '')}
🪪 کد ملی: {user_data.get('national_id', 'ثبت نشده')}

📌 موضوع: {subject}
{detail}"""
    send_message(ADMIN_CHAT_ID, text)
def forward_photo_to_admin(chat_id, file_id, caption=""):
    url = f"{API_URL}/sendPhoto"
    data = {"chat_id": ADMIN_CHAT_ID, "photo": file_id, "caption": caption}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"خطا در ارسال عکس: {e}")

def show_main_menu(chat_id, name=""):
    greeting = f"{name} عزیز، " if name else ""
    keyboard = make_keyboard([
        [("❓ سوال حقوقی دارم", "menu_1")],
        [("📄 تنظیم سند می‌خوام", "menu_2")],
        [("⚖️ پرونده دارم", "menu_3")],
        [("💰 پیگیری پرداخت", "menu_4")],
        [("📋 قیمت‌ها و خدمات", "menu_5")],
    ])
    send_message(chat_id, f"{greeting}لطفاً یکی از خدمات زیر را انتخاب کنید:", keyboard)


def handle_registration(chat_id, text, step):
    if step == "get_name":
        users[chat_id]["name"] = text
        users[chat_id]["step"] = "get_family"
        send_message(chat_id, "نام خانوادگی خود را وارد کنید:")

    elif step == "get_family":
        users[chat_id]["family"] = text
        users[chat_id]["step"] = "get_phone"
        send_message(chat_id, "شماره تماس خود را وارد کنید:")

    elif step == "get_phone":
        users[chat_id]["phone"] = text
        users[chat_id]["step"] = "get_national_id"
        keyboard = make_keyboard([[("رد کردن (اختیاری)", "skip_national_id")]])
        send_message(chat_id, "کد ملی خود را وارد کنید:\n(اختیاری)", keyboard)

    elif step == "get_national_id":
        users[chat_id]["national_id"] = text
        users[chat_id]["step"] = "main_menu"
        name = users[chat_id]["name"]
        send_message(chat_id, "✅ ثبت‌نام شما کامل شد!")
        show_main_menu(chat_id, name)


def handle_question_menu(chat_id):
    keyboard = make_keyboard([
        [("پرسش عمومی (۵۰ هزار تومان)", "q_general")],
        [("پرسش تخصصی (۵۰۰ هزار تومان)", "q_special")],
        [("🔙 بازگشت", "back_main")],
    ])
    send_message(chat_id, "❓ نوع سوال خود را انتخاب کنید:", keyboard)


def handle_document_menu(chat_id):
    keyboard = make_keyboard([
        [("دادخواست (۶۹۷ هزار)", "doc_1")],
        [("شکواییه (۹۴۹ هزار)", "doc_2")],
        [("اظهارنامه (۴۹۷ هزار)", "doc_3")],
        [("قرارداد (از ۷۹۹ هزار)", "doc_4")],
        [("لایحه حقوقی (۶۹۷ هزار)", "doc_5")],
        [("لایحه کیفری (۹۴۹ هزار)", "doc_6")],
        [("🔙 بازگشت", "back_main")],
    ])
    send_message(chat_id, "📄 نوع سند مورد نیاز را انتخاب کنید:", keyboard)


def handle_case_menu(chat_id):
    keyboard = make_keyboard([
        [("داوری", "case_arbitration")],
        [("وکالت مدنی", "case_civil")],
        [("🔙 بازگشت", "back_main")],
    ])
    send_message(chat_id, "⚖️ نوع پرونده خود را انتخاب کنید:", keyboard)


def show_prices(chat_id):
    keyboard = make_keyboard([[("🔙 بازگشت به منو", "back_main")]])
    send_message(chat_id, f"""📋 قیمت‌ها و خدمات رهیار قانون

❓ سوال حقوقی:
• پرسش عمومی: ۵۰ هزار تومان
• پرسش تخصصی: ۵۰۰ هزار تومان

📄 تنظیم سند:
• دادخواست: ۶۹۷ هزار تومان
• شکواییه: ۹۴۹ هزار تومان
• اظهارنامه: ۴۹۷ هزار تومان
• قرارداد: از ۷۹۹ هزار تومان
• لایحه حقوقی: ۶۹۷ هزار تومان
• لایحه کیفری: ۹۴۹ هزار تومان

⚖️ پرونده (توافقی):
• داوری
• وکالت مدنی

📞 تماس: 09931012756""", keyboard)


def handle_payment_follow(chat_id):
    send_message(chat_id, "💰 پیگیری پرداخت\n\nلطفاً تصویر رسید پرداخت خود را ارسال کنید:")


DOCUMENT_NAMES = {
    "doc_1": ("دادخواست", "۶۹۷,۰۰۰"),
    "doc_2": ("شکواییه", "۹۴۹,۰۰۰"),
    "doc_3": ("اظهارنامه", "۴۹۷,۰۰۰"),
    "doc_4": ("قرارداد", "۷۹۹,۰۰۰"),
    "doc_5": ("لایحه حقوقی", "۶۹۷,۰۰۰"),
    "doc_6": ("لایحه کیفری", "۹۴۹,۰۰۰"),
}


def handle_callback(chat_id, callback_data):
    if chat_id not in users:
        return

    if callback_data == "back_main":
        users[chat_id]["step"] = "main_menu"
        show_main_menu(chat_id)

    elif callback_data == "skip_national_id":
        users[chat_id]["national_id"] = "ثبت نشده"
        users[chat_id]["step"] = "main_menu"
        name = users[chat_id]["name"]
        send_message(chat_id, "✅ ثبت‌نام شما کامل شد!")
        show_main_menu(chat_id, name)

    elif callback_data == "menu_1":
        users[chat_id]["step"] = "question_menu"
        handle_question_menu(chat_id)

    elif callback_data == "menu_2":
        users[chat_id]["step"] = "document_menu"
        handle_document_menu(chat_id)

    elif callback_data == "menu_3":
        users[chat_id]["step"] = "case_menu"
        handle_case_menu(chat_id)

    elif callback_data == "menu_4":
        users[chat_id]["step"] = "payment_follow"
        handle_payment_follow(chat_id)

    elif callback_data == "menu_5":
        show_prices(chat_id)

    elif callback_data == "q_general":
        users[chat_id]["step"] = "waiting_payment_question"
        users[chat_id]["question_type"] = "عمومی"
        users[chat_id]["question_price"] = "۵۰ هزار تومان"
        send_message(chat_id, f"""✅ پرسش عمومی انتخاب شد

لطفاً مبلغ ۵۰,۰۰۰ تومان به شماره کارت زیر واریز کنید:

💳 {CARD_NUMBER}

بعد از پرداخت، تصویر رسید را ارسال کنید:""")

    elif callback_data == "q_special":
        users[chat_id]["step"] = "waiting_payment_question"
        users[chat_id]["question_type"] = "تخصصی"
        users[chat_id]["question_price"] = "۵۰۰ هزار تومان"
        send_message(chat_id, f"""✅ پرسش تخصصی انتخاب شد

لطفاً مبلغ ۵۰۰,۰۰۰ تومان به شماره کارت زیر واریز کنید:

💳 {CARD_NUMBER}

بعد از پرداخت، تصویر رسید را ارسال کنید:""")

    elif callback_data in DOCUMENT_NAMES:
        doc_name, doc_price = DOCUMENT_NAMES[callback_data]
        users[chat_id]["step"] = "waiting_payment_document"
        users[chat_id]["document_type"] = doc_name
        users[chat_id]["document_price"] = doc_price
        send_message(chat_id, f"""✅ {doc_name} انتخاب شد

لطفاً مبلغ {doc_price} تومان به شماره کارت زیر واریز کنید:

💳 {CARD_NUMBER}

بعد از پرداخت، تصویر رسید را ارسال کنید:""")

    elif callback_data == "case_arbitration":
        users[chat_id]["step"] = "case_detail"
        users[chat_id]["case_type"] = "داوری"
        send_message(chat_id, "⚖️ پرونده داوری\n\nلطفاً توضیح مختصری از موضوع پرونده خود بنویسید:")

    elif callback_data == "case_civil":
        users[chat_id]["step"] = "case_detail"
        users[chat_id]["case_type"] = "وکالت مدنی"
        send_message(chat_id, "👨‍💼 وکالت مدنی\n\nلطفاً توضیح مختصری از موضوع پرونده خود بنویسید:")


def handle_text_message(chat_id, text, photo, photo_file_id=None):
    step = users[chat_id].get("step", "main_menu")

    if step in ["get_name", "get_family", "get_phone", "get_national_id"]:
        handle_registration(chat_id, text, step)

    elif step == "case_detail":
        case_type = users[chat_id].get("case_type", "")
        forward_to_admin(users[chat_id], f"پرونده {case_type}", f"📝 توضیحات: {text}")
        users[chat_id]["step"] = "main_menu"
        keyboard = make_keyboard([[("🔙 بازگشت به منو", "back_main")]])
        send_message(chat_id, "✅ درخواست شما ثبت شد\n\nبه زودی با شما تماس گرفته می‌شود.", keyboard)

    elif step == "waiting_payment_question":
        if photo:
            question_type = users[chat_id].get("question_type", "")
            question_price = users[chat_id].get("question_price", "")
            forward_to_admin(users[chat_id], f"پرداخت سوال {question_type} - {question_price}", "📎 رسید پرداخت زیر است")
            if photo_file_id:
                forward_photo_to_admin(photo_file_id, f"رسید پرداخت - {question_type}")
                users[chat_id]["step"] = "asking_question"
        else:
            send_message(chat_id, "لطفاً تصویر رسید پرداخت را ارسال کنید:")

    elif step == "asking_question":
        question_type = users[chat_id].get("question_type", "")
        forward_to_admin(users[chat_id], f"سوال {question_type}", f"❓ سوال: {text}")
        users[chat_id]["step"] = "main_menu"
        keyboard = make_keyboard([[("🔙 بازگشت به منو", "back_main")]])
        send_message(chat_id, "✅ سوال شما ثبت شد\n\nبه زودی پاسخ داده خواهد شد.", keyboard)

    elif step == "waiting_payment_document":
        if photo:
            doc_type = users[chat_id].get("document_type", "")
            doc_price = users[chat_id].get("document_price", "")
            forward_to_admin(users[chat_id], f"پرداخت {doc_type} - {doc_price} تومان", "📎 رسید پرداخت زیر است")
            if photo_file_id:
                forward_photo_to_admin(photo_file_id, f"رسید پرداخت - {doc_type}")
                users[chat_id]["step"] = "document_detail"
                send_message(chat_id, "✅ رسید دریافت شد!\n\nلطفاً اطلاعات مورد نیاز برای تنظیم سند را بنویسید:")
        else:
            send_message(chat_id, "لطفاً تصویر رسید پرداخت را ارسال کنید:")

    elif step == "document_detail":
        doc_type = users[chat_id].get("document_type", "")
        forward_to_admin(users[chat_id], f"اطلاعات {doc_type}", f"📝 اطلاعات: {text}")
        users[chat_id]["step"] = "main_menu"
        keyboard = make_keyboard([[("🔙 بازگشت به منو", "back_main")]])
        send_message(chat_id, "✅ اطلاعات شما ثبت شد\n\nسند شما در اسرع وقت تنظیم خواهد شد.", keyboard)

    elif step == "payment_follow":
        if photo:
            forward_to_admin(users[chat_id], "پیگیری پرداخت", "📎 رسید پرداخت ارسال شد")
            users[chat_id]["step"] = "main_menu"
            keyboard = make_keyboard([[("🔙 بازگشت به منو", "back_main")]])
            send_message(chat_id, "✅ رسید شما دریافت شد و بررسی خواهد شد.", keyboard)
        else:
            send_message(chat_id, "لطفاً تصویر رسید پرداخت را ارسال کنید:")

    else:
        show_main_menu(chat_id)


def handle_message(message):
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "")
    photo = message.get("photo")
    photo_file_id = None
    if photo:
        photo_file_id = photo[-1].get("file_id") if isinstance(photo, list) else None
    if not chat_id:
        return
        
    if text == "/myid":
        send_message(chat_id, f"آیدی چت شما: {chat_id}")
        return
    
    if text == "/reset":
        users[chat_id] = {"step": "get_name"}
        send_message(chat_id, "ثبت‌نام مجدد شروع شد!\n\nلطفاً نام خود را وارد کنید:")
        return

    if chat_id not in users:
        users[chat_id] = {"step": "get_name"}
        send_message(chat_id, """سلام! 👋
به ربات رهیار قانون خوش آمدید ⚖️

برای شروع لطفاً نام خود را وارد کنید:""")
        return

    handle_text_message(chat_id, text, photo, photo_file_id)


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
    url = f"{API_URL}/getUpdates"
    data = {"offset": offset}
    try:
        response = requests.post(url, data=data, timeout=10)
        return response.json()
    except Exception as e:
        print(f"خطا در دریافت آپدیت: {e}")
        return {"ok": False}


def main():
    print("ربات رهیار قانون در حال اجراست...")
    offset = 0
    while True:
        try:
            updates = get_updates(offset)
            if updates.get("ok") and updates.get("result"):
                for update in updates["result"]:
                    offset = update["update_id"] + 1
                    handle_update(update)
        except Exception as e:
            print(f"خطا: {e}")
        time.sleep(2)


if __name__ == "__main__":
    main()
