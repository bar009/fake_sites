# Fake Shop Checker — מרכז חקירת אתרי התחזות

מערכת מחקר מקומית שמאתרת אתרי `.shop` החשודים בהתחזות למותגים. היא מחפשת
חתימות של תבניות זיוף, מצלמת את הדף בדפדפן מבודד, בודקת את גיל הדומיין ומציגה
ציון סיכון מוסבר. שווי השוק של חברת האם משמש לציון עדיפות נפרד ואינו משנה את
הקביעה אם אתר חשוד.

> המערכת מיועדת לתעדוף וסקירה אנושית, לא לקביעה אוטומטית שאתר הוא מזויף.

## התקנה

```powershell
cd C:\dev\fake-shop-checker
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\playwright install chromium
```

## ממשק Web מקומי

```powershell
.venv\Scripts\python -m fakeshop.web
```

לאחר ההפעלה פותחים `http://127.0.0.1:8000`. השרת מאזין ל־localhost בלבד.

בממשק אפשר:

- להעלות CSV של מותגים.
- לסרוק מותג יחיד או URL מסוים.
- לעקוב אחר עבודות רקע, לעצור ולהמשיך אותן.
- לראות צילומי מסך, ציון סיכון, ראיות וציון עדיפות.
- לסמן ממצא כלא נבדק, חשוד מאומת, false positive או דורש חקירה.
- לייצא JSON, Excel או HTML.

הנתונים נשמרים ב־`data/` ואינם עולים ל־Git. נתוני שווי מתקבלים דרך `yfinance`
מ־Yahoo Finance באופן לא רשמי, מיועדים למחקר אישי ועלולים להיות חסרים או
מעוכבים. המקור וזמן העדכון מוצגים בכל מסך.

## CSV

העמודה `brand` היא חובה. יתר העמודות אופציונליות:

```csv
brand,topic,parent_company,ticker,official_domain
Nike,sportswear,Nike Inc,NKE,nike.com
Lego,toys,,,
```

ערכי `parent_company` ו־`ticker` מפורשים גוברים על המיפוי האוטומטי.

המאגר כולל גם את `brands_1000.csv`: קטלוג מורחב של 1,000 יעדי סריקה ב־50
תחומים. הוא משלב 300 מותגים צרכניים בעלי סיכון התחזות גבוה עם 700 חברות
ציבוריות גדולות, שנבחרו לפי שווי שוק מנתוני [Nasdaq Stock Screener](https://www.nasdaq.com/market-activity/stocks/screener)
ונוקו מכפילויות של סוגי מניה וניירות ערך. שדה `official_domain` נשאר ריק כאשר
לא היה מקור מאומת, כדי לא להכניס כתובות מנוחשות.

## CLI קיים

דרך ההפעלה המקורית נשמרה:

```powershell
.venv\Scripts\python check_brands.py brands.csv
```

אפשרויות שימושיות:

- `--top 5` — מספר תוצאות למותג.
- `--brand Nike` — מותג יחיד מתוך הקובץ.
- `--topic watches` — נושא יחיד.
- `--resume` — המשך הריצה האחרונה שנקטעה.
- `--provider brave` — שימוש ב־Brave Search API עם `BRAVE_API_KEY` בקובץ `.env`.

פלטי CLI נשמרים תחת `runs/` ומוחרגים מ־Git.

## בטיחות

האתרים הנבדקים עלולים להיות עוינים. המערכת:

- מציגה URL כטקסט להעתקה ולא כקישור לחיץ.
- חוסמת localhost, כתובות פרטיות, link-local ופורטים שאינם 80/443.
- יוצרת browser context זמני לכל אתר וחוסמת הורדות ו־service workers.
- אינה לוחצת, ממלאת טפסים או מורידה קבצים.

## בדיקות

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

## Next steps

- מחקר חתימות חיפוש נוספות מעבר ל־`What Our Customer Say`.
- הרחבה מבוקרת ל־`.store`, `.online`, `.site` וסיומות נוספות.
- Certificate Transparency, דומיינים דומים, passive DNS ומאגרי abuse.
- ניטור מתוזמן, השוואה בין ריצות והתראות.
- מקור פיננסי מורשה אם המערכת תעבור לשימוש מסחרי.
