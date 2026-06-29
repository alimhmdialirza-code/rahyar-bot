import requests
import time

TOKEN = "76366599:pe/VsYAJDF-CAyoZqS0ZG-dLc5JHGgv{-Pn57bS8y[9-OQHNF2uaMV-EAdWCli2t9-sxh?jTd4N4-y?dZe1T@bw-Qar8U#2zTW-qkf&~Do6wt-6U^3TzF5t9-fmg8h37IUP-7bwz2*aaBt-ovP%ONHM(a-jX$gNZoEBh-tx}W"
API_URL = f"https://eitaayar.ir/api/{TOKEN}"
ADMIN_CHAT_ID = "rahyar_ghanon"
CARD_NUMBER = "6221061237894102"

users = {}


def send_message(chat_id, text):
    url = f"{API_URL}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"خطا در ارسال پیام: {e}")


def forward_to_admin(user_data, subject, detail=""):
    text = f"""📨 درخواست جدید از ربات رهیار قانون

👤 نام: {user_data.get('name', '')} {user_data.get('family', '')}
📞 تماس: {user_data.get('phone', '')}
🪪 کد ملی: {user_data.get('national_id', 'ثبت نشده')}

📌 موضوع: {subject}
{detail}"""
    send_message(ADMIN_CHAT_ID, text)


def show_main_menu(chat_id, name=""):
    greeting = f"{name} عزیز، " if name else ""
    send_message(chat_id, f"""{greeting}لطفاً یکی از خدمات زیر را انتخاب کنید:

1️⃣ سوال حقوقی دارم
2️⃣ تنظیم سند می‌خوام
3️⃣ پرونده دارم
4️⃣ پیگیری پرداخت
5️⃣ قیمت‌ها و خدمات

عدد مورد نظر را بفرستید:""")


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
        send_message(chat_id, "کد ملی خود را وارد کنید:\n(اختیاری — برای رد کردن عدد ۰ بزنید)")

    elif step == "get_national_id":
        users[chat_id]["national_id"] = text if text != "0" else "ثبت نشده"
        users[chat_id]["step"] = "main_menu"
        name = users[chat_id]["name"]
        send_message(chat_id, f"✅ ثبت‌نام شما کامل شد!")
        show_main_menu(chat_id, name)


def handle_main_menu(chat_id, text):
    if text == "1":
        users[chat_id]["step"] = "question_menu"
        send_message(chat_id, """❓ نوع سوال خود را انتخاب کنید:

1️⃣ پرسش عمومی (۵۰ هزار تومان)
2️⃣ پرسش تخصصی (۵۰۰ هزار تومان)

عدد مورد نظر را بفرستید:
(برای بازگشت عدد ۰ بزنید)""")

    elif text == "2":
        users[chat_id]["step"] = "document_menu"
        send_message(chat_id, """📄 نوع سند مورد نیاز را انتخاب کنید:

1️⃣ دادخواست (۶۹۷ هزار تومان)
2️⃣ شکواییه (۹۴۹ هزار تومان)
3️⃣ اظهارنامه (۴۹۷ هزار تومان)
4️⃣ قرارداد (از ۷۹۹ هزار تومان)
5️⃣ لایحه حقوقی (۶۹۷ هزار تومان)
6️⃣ لایحه کیفری (۹۴۹ هزار تومان)

عدد مورد نظر را بفرستید:
(برای بازگشت عدد ۰ بزنید)""")

    elif text == "3":
        users[chat_id]["step"] = "case_menu"
        send_message(chat_id, """⚖️ نوع پرونده خود را انتخاب کنید:

1️⃣ داوری
2️⃣ وکالت مدنی (مراجع شبه‌قضایی)

عدد مورد نظر را بفرستید:
(برای بازگشت عدد ۰ بزنید)""")

    elif text == "4":
        users[chat_id]["step"] = "payment_follow"
        send_message(chat_id, """💰 پیگیری پرداخت

لطفاً تصویر رسید پرداخت خود را ارسال کنید:
(برای بازگشت عدد ۰ بزنید)""")

    elif text == "5":
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

📞 تماس: 09931012756

برای بازگشت به منو عدد ۰ بزنید:""")

    else:
        send_message(chat_id, "لطفاً یک عدد بین ۱ تا ۵ بفرستید:")


def handle_question_menu(chat_id, text):
    if text == "1":
        users[chat_id]["step"] = "waiting_payment_question"
        users[chat_id]["question_type"] = "عمومی"
        users[chat_id]["question_price"] = "۵۰ هزار تومان"
        send_message(chat_id, f"""✅ پرسش عمومی انتخاب شد

لطفاً مبلغ ۵۰,۰۰۰ تومان به شماره کارت زیر واریز کنید:

💳 {CARD_NUMBER}

بعد از پرداخت، تصویر رسید را ارسال کنید:""")

    elif text == "2":
        users[chat_id]["step"] = "waiting_payment_question"
        users[chat_id]["question_type"] = "تخصصی"
        users[chat_id]["question_price"] = "۵۰۰ هزار تومان"
        send_message(chat_id, f"""✅ پرسش تخصصی انتخاب شد

لطفاً مبلغ ۵۰۰,۰۰۰ تومان به شماره کارت زیر واریز کنید:

💳 {CARD_NUMBER}

بعد از پرداخت، تصویر رسید را ارسال کنید:""")

    elif text == "0":
        users[chat_id]["step"] = "main_menu"
        show_main_menu(chat_id)

    else:
        send_message(chat_id, "لطفاً عدد ۱ یا ۲ بفرستید:")


def handle_document_menu(chat_id, text):
    documents = {
        "1": ("دادخواست", "۶۹۷,۰۰۰"),
        "2": ("شکواییه", "۹۴۹,۰۰۰"),
        "3": ("اظهارنامه", "۴۹۷,۰۰۰"),
        "4": ("قرارداد", "۷۹۹,۰۰۰"),
        "5": ("لایحه حقوقی", "۶۹۷,۰۰۰"),
        "6": ("لایحه کیفری", "۹۴۹,۰۰۰"),
    }
    if text in documents:
        doc_name, doc_price = documents[text]
        users[chat_id]["step"] = "waiting_payment_document"
        users[chat_id]["document_type"] = doc_name
        users[chat_id]["document_price"] = doc_price
        send_message(chat_id, f"""✅ {doc_name} انتخاب شد

لطفاً مبلغ {doc_price} تومان به شماره کارت زیر واریز کنید:

💳 {CARD_NUMBER}

بعد از پرداخت، تصویر رسید را ارسال کنید:""")

    elif text == "0":
        users[chat_id]["step"] = "main_menu"
        show_main_menu(chat_id)

    else:
        send_message(chat_id, "لطفاً یک عدد بین ۱ تا ۶ بفرستید:")


def handle_case_menu(chat_id, text):
    if text == "1":
        users[chat_id]["step"] = "case_detail"
        users[chat_id]["case_type"] = "داوری"
        send_message(chat_id, """⚖️ پرونده داوری

لطفاً توضیح مختصری از موضوع پرونده خود بنویسید:""")

    elif text == "2":
        users[chat_id]["step"] = "case_detail"
        users[chat_id]["case_type"] = "وکالت مدنی"
        send_message(chat_id, """👨‍💼 وکالت مدنی (مراجع شبه‌قضایی)

لطفاً توضیح مختصری از موضوع پرونده خود بنویسید:""")

    elif text == "0":
        users[chat_id]["step"] = "main_menu"
        show_main_menu(chat_id)

    else:
        send_message(chat_id, "لطفاً عدد ۱ یا ۲ بفرستید:")


def handle_message(message):
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "")
    photo = message.get("photo")

    if not chat_id:
        return

    if chat_id not in users:
        users[chat_id] = {"step": "get_name"}
        send_message(chat_id, """سلام! 👋
به ربات رهیار قانون خوش آمدید ⚖️

برای شروع لطفاً نام خود را وارد کنید:""")
        return

    step = users[chat_id].get("step", "main_menu")

    if text == "0" and step not in ["get_name", "get_family", "get_phone", "get_national_id"]:
        users[chat_id]["step"] = "main_menu"
        show_main_menu(chat_id)
        return

    if step in ["get_name", "get_family", "get_phone", "get_national_id"]:
        handle_registration(chat_id, text, step)

    elif step == "main_menu":
        handle_main_menu(chat_id, text)

    elif step == "question_menu":
        handle_question_menu(chat_id, text)

    elif step == "document_menu":
        handle_document_menu(chat_id, text)

    elif step == "case_menu":
        handle_case_menu(chat_id, text)

    elif step == "case_detail":
        case_type = users[chat_id].get("case_type", "")
        forward_to_admin(users[chat_id], f"پرونده {case_type}", f"📝 توضیحات: {text}")
        users[chat_id]["step"] = "main_menu"
        send_message(chat_id, """✅ درخواست شما ثبت شد

به زودی با شما تماس گرفته می‌شود و هزینه توافق خواهد شد.

برای بازگشت به منو عدد ۰ بزنید:""")

    elif step == "waiting_payment_question":
        if photo:
            question_type = users[chat_id].get("question_type", "")
            question_price = users[chat_id].get("question_price", "")
            forward_to_admin(users[chat_id], f"پرداخت سوال {question_type} - {question_price}", "📎 رسید پرداخت ارسال شد")
            users[chat_id]["step"] = "asking_question"
            send_message(chat_id, "✅ رسید دریافت شد!\n\nحالا سوال خود را بنویسید:")
        else:
            send_message(chat_id, "لطفاً تصویر رسید پرداخت را ارسال کنید:")

    elif step == "asking_question":
        question_type = users[chat_id].get("question_type", "")
        forward_to_admin(users[chat_id], f"سوال {question_type}", f"❓ سوال: {text}")
        users[chat_id]["step"] = "main_menu"
        send_message(chat_id, """✅ سوال شما ثبت شد

به زودی پاسخ داده خواهد شد.

برای بازگشت به منو عدد ۰ بزنید:""")

    elif step == "waiting_payment_document":
        if photo:
            doc_type = users[chat_id].get("document_type", "")
            doc_price = users[chat_id].get("document_price", "")
            forward_to_admin(users[chat_id], f"پرداخت {doc_type} - {doc_price} تومان", "📎 رسید پرداخت ارسال شد")
            users[chat_id]["step"] = "document_detail"
            send_message(chat_id, "✅ رسید دریافت شد!\n\nلطفاً اطلاعات مورد نیاز برای تنظیم سند را بنویسید:")
        else:
            send_message(chat_id, "لطفاً تصویر رسید پرداخت را ارسال کنید:")

    elif step == "document_detail":
        doc_type = users[chat_id].get("document_type", "")
        forward_to_admin(users[chat_id], f"اطلاعات {doc_type}", f"📝 اطلاعات: {text}")
        users[chat_id]["step"] = "main_menu"
        send_message(chat_id, """✅ اطلاعات شما ثبت شد

سند شما در اسرع وقت تنظیم و ارسال خواهد شد.

برای بازگشت به منو عدد ۰ بزنید:""")

    elif step == "payment_follow":
        if photo:
            forward_to_admin(users[chat_id], "پیگیری پرداخت", "📎 رسید پرداخت ارسال شد")
            users[chat_id]["step"] = "main_menu"
            send_message(chat_id, "✅ رسید شما دریافت شد و بررسی خواهد شد.\n\nبرای بازگشت به منو عدد ۰ بزنید:")
        else:
            send_message(chat_id, "لطفاً تصویر رسید پرداخت را ارسال کنید:")


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
                    if "message" in update:
                        handle_message(update["message"])
        except Exception as e:
            print(f"خطا: {e}")
        time.sleep(2)


if __name__ == "__main__":
    main()
