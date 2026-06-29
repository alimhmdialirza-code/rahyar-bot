import requests
import time

TOKEN = "741504271:glrJAftdMTf2g10n8GCGAN2iFPeveKDehc8"
API_URL = f"https://tapi.bale.ai/bot{TOKEN}"
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
    text = f"""📨 درخواست جدید ا ربات رهیار قانون

👤 نام: {user_data.get('name', '')} {user_data.get('family', '')}
📞 تماس: {user_data.get('phone', '')}
🪪 کد ملی: {user_data.get('national_id', 'ثبت نشده')}


📌 موضوع: {subject}
{detail}"""
    send_messageADMIN_CHAT_ID, text)


def show_main_menu(chat_id, name=""):
    greeting = f"{name} عزیز، " if name else ""
    send_message(chat_id, f"""{greeting}لطفاً یکی از خدمات زیر را انتخاب کنید:

1️⃣ سوال حقوقی دارم
2️⃣ تنظیم سند می‌خوام
3️⃣ پرونده دارم
4️⃣ پیگیری پرداخت
5️⃣ قیمت‌ها و خدمات

عدد مورد نظر را بفرستید:""")


def reset_user(chat_id):
    if chat_id in users:
        del users[chat_id]


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
        send_message(chat_id, "✅ ثبت‌نام شما کامل شد!")
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

1️ دادخواست (۶۹۷ هزار تومان)
2️⃣ شکواییه (۹۴۹ هزار تومان)
3️⃣ اظهارنامه (۴۹۷ هزار تومان)
4️⃣ قرارداد (از ۷۹۹ هزار تومان)
5️⃣  data=data, timeout=10)
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


if name == "__main__":
    main()
