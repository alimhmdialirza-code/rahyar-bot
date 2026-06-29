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


if name == "__main__":
    main()
