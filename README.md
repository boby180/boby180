# בוט חדשות טובות לטלגרם ☀️

כל בוקר ב-08:00 (שעון ישראל) הבוט שולח לך בטלגרם 5 כתבות חדשות טובות
מאתרים כמו Good News Network, Positive News ו-Reasons to be Cheerful.
ההרצה מתבצעת אוטומטית ב-GitHub Actions — לא צריך שרת.

## התקנה (פעם אחת)

1. **טוקן לבוט** – ב-@BotFather בטלגרם (`/newbot` אם עוד אין לך בוט) והעתק את הטוקן.
2. **Chat ID** – שלח הודעה כלשהי לבוט שלך, ואז פתח בדפדפן:
   `https://api.telegram.org/bot<TOKEN>/getUpdates` ומצא את `"chat":{"id": ...}`.
3. ב-GitHub: **Settings → Secrets and variables → Actions → New repository secret**, והוסף:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. מזג את הקוד לענף הראשי (`main`) – תזמונים של GitHub Actions רצים רק מהענף הראשי.
5. בדיקה: **Actions → Daily good news to Telegram → Run workflow** שולח הודעה מיד.

## התאמות

- מספר כתבות / שעה / אזור זמן: משתני הסביבה `NEWS_COUNT`, `SEND_HOUR`, `TIMEZONE` ב-`good_news_bot.py`.
- מקורות: רשימת `FEEDS` ב-`good_news_bot.py` (כל פיד RSS/Atom).

> GitHub Actions עלול לעכב הרצות מתוזמנות בכמה דקות.
