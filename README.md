# Fake Shop Checker — בודק אתרי מותגים מזויפים

מקבל רשימת מותגים, מריץ עבור כל מותג את החיפוש
`site:.shop "What Are The Costumers Say" "<brand>"` (השגיאה "Costumers" היא בכוונה —
זו טביעת האצבע של תבנית האתרים המזויפים), נכנס ל-3 התוצאות הראשונות,
מצלם מסך מלא, בודק WHOIS (גיל הדומיין), ושומר הכל לתיקיית ריצה.

## התקנה (פעם אחת)

```powershell
cd C:\dev\fake-shop-checker
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\playwright install chromium
```

## הרצה

```powershell
.venv\Scripts\python check_brands.py brands.csv
```

אפשרויות:

- `--top 5` — כמה תוצאות לכל מותג (ברירת מחדל 3)
- `--brand Nike` — להריץ רק מותג אחד מהקובץ
- `--provider brave` — לחפש דרך Brave Search API במקום DuckDuckGo
  (דורש `BRAVE_API_KEY` בקובץ `.env`, ראו `.env.example`)

## קובץ הקלט

`brands.csv` — שורת כותרת ואז מותג בכל שורה:

```csv
brand,topic
Nike,sportswear
Lego,toys
```

עמודת `topic` אופציונלית — נועדה לקטלוג מאגרי מותגים לפי נושאים בהמשך.

## התוצאות

כל ריצה נשמרת ב-`runs\<תאריך>_<שעה>\`:

| קובץ | מה יש בו |
|------|----------|
| `report.html` | דו"ח ויזואלי — תמונות ממוזערות לחיצות, קישור לאתר, גיל דומיין, דגלים. **זה הקובץ לסקירה מהירה.** |
| `results.xlsx` | טבלת אקסל — שורה לכל תוצאה; שורות חשודות מסומנות באדום |
| `screenshots\` | צילומי מסך מלאים (`מותג_דירוג_דומיין.png`) |
| `results.json` | הנתונים הגולמיים — לעיבוד עתידי |

## סימוני חשד

- דומיין צעיר מ-180 יום (לפי RDAP/WHOIS) — אתרים מזויפים כמעט תמיד טריים.
- שגיאות (אתר מת, timeout) נרשמות בעמודת Error ולא עוצרות את הריצה.

## אזהרה

האתרים הנבדקים עוינים. הכלי רק טוען את הדף ומצלם — לא ללחוץ, לא למלא טפסים,
ולא להוריד שום דבר מהאתרים האלה גם ידנית.
