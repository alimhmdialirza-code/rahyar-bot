cat > /home/claude/bot.py << 'ENDOFFILE'
import requests
import time
import json

TOKEN = "741504271:glrJAftdMTf2g10n8GCGAN2iFPeveKDehc8"
API_URL = f"https://tapi.bale.ai/bot{TOKEN}"
ADMIN_CHAT_ID = "rahyar_ghanon"
CARD_NUMBER = "6221061237894102"

users = {}


def send_message(chat_id, text, reply_markup=None):
    url = f"{API_URL}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"خطا در ارسال پیام: {e}")


def make_keyboard(buttons):
    keyboard = []
    for row in buttons:
        keyboard.append([{"text": btn} for btn in row])
    return {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": False}


def remove_keyboard():
    return {"remove_keyboard": True}


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
    keyboard = make_keyboard([
        ["❓ سوال حقوقی دارم"],
        ["📄 تنظیم سند می‌خوام"],
        ["⚖️ پرونده دارم"],
        ["💰 پیگیری پرداخت"],
        ["📋 قیمت‌ها و خدمات"]
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
        send_message(chat_id, "کد ملی خود را وارد کنید:\n(اختیاری — برای رد کردن عدد ۰ بزنید)")

    elif step == "get_national_id":
        users[chat_id]["national_id"] = text if text != "0" else "ثبت نشده"
        users[chat_id]["step"] = "main_menu"
        name = users[chat_id]["name"]
        send_message(chat_id, f"✅ ثبت‌نام شما کامل شد!")
        show_main_menu(chat_id, name)


def handle_main_menu(chat_id, text):
    if "سوال" in text:
        users[chat_id]["step"] = "question_menu"
        keyboard = make_keyboard([
            ["💬 پرسش عمومی (۵۰ هزار تومان)"],
            ["🔍 پرسش تخصصی (۵۰۰ هزار تومان)"],
            ["🔙 بازگشت"]
        ])
        send_message(chat_id, "❓ نوع سوال خود را انتخاب کنید:", keyboard)

    elif "سند" in text:
        users[chat_id]["step"] = "document_menu"
        keyboard = make_keyboard([
            ["📄 دادخواست (۶۹۷ هزار)"],
            ["📄 شکواییه (۹۴۹ هزار)"],
            ["📄 اظهارنامه (۴۹۷ هزار)"],
            ["📄 قرارداد (از ۷۹۹ هزار)"],
            ["📄 لایحه حقوقی (۶۹۷ هزار)"],
            ["📄 لایحه کیفری (۹۴۹ هزار)"],
            ["🔙 بازگشت"]
        ])
        send_message(chat_id, "📄 نوع سند مورد نیاز را انتخاب کنید:", keyboard)

    elif "پرونده" in text:
        users[chat_id]["step"] = "case_menu"
        keyboard = make_keyboard([
            ["⚖️ داوری"],
            ["👨‍💼 وکالت مدنی (مراجع شبه‌قضایی)"],
            ["🔙 بازگشت"]
        ])
        send_message(chat_id, "⚖️ نوع پرونده خود را انتخاب کنید:", keyboard)

    elif "پیگیری" in text:
        users[chat_id]["step"] = "payment_follow"
        keyboard = make_keyboard([["🔙 بازگشت"]])
        send_message(chat_id, "💰 لطفاً تصویر رسید پرداخت خود را ارسال کنید:", keyboard)

    elif "قیمت" in text:
        send_message(chat_id, f"""📋 قیمت‌ها و خدمات رهیار قانون

❓ سوال حقوقی:
• پرسش عمومی: ۵۰ هزار تومان
• پرسش تخصصی: ۵۰۰ هزار تومانs.post(url, data=data, timeout=10)
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
