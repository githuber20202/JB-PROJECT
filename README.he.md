<div align="center">

# AWS Resources Viewer (Flask + Docker)

יישום Flask המציג בטבלאות משאבי AWS (EC2, VPCs, Load Balancers, AMIs) באמצעות boto3. היישום מאזין על פורט 5001 וקורא פרטי גישה ממשתני סביבה.

</div>

## תוכן עניינים
- [סקירה](#סקירה)
- [יכולות](#יכולות)
- [קבצים עיקריים](#קבצים-עיקריים)
- [תצורה](#תצורה)
- [התקנה והרצה](#התקנה-והרצה)
- [פריסה ל-EC2](#פריסה-ל-ec2)
- [ארכיטקטורה ותרשימי זרימה](#ארכיטקטורה-ותרשימי-זרימה)
- [Docker Build (Multi-Stage)](#docker-build-multi-stage)
- [פתרון בעיות](#פתרון-בעיות)
- [קרדיט](#קרדיט)

---

## סקירה
היישום מגיש דף HTML עם ארבע טבלאות: EC2 Instances, VPCs, Load Balancers ו‑AMIs בבעלות החשבון. האזור המוגדר הוא `us-east-1`. המימוש מיועד לריצה כקונטיינר Docker קל מקומית או על EC2.

## יכולות
- EC2 Instances: מצב, סוג ו‑Public IP.
- VPCs: מזהה וטווח CIDR.
- Load Balancers (ALB/NLB): שם ו‑DNS.
- AMIs בבעלות החשבון.
- טיפול שגיאות ידידותי כך שהעמוד נטען גם ללא קרדנציאלס/הרשאות מלאות.

## קבצים עיקריים
- `app.py` — יישום Flask: קריאות boto3 והצגת הטבלאות.
- `Dockerfile` — Multi‑stage build: בניית wheels בשלב Builder ושכבת Runtime רזה.
- `requirements.txt` — תלותים (Flask, boto3).
- `image.png` — מסך לדוגמה של העמוד.

## תצורה
- משתני סביבה:
  - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — אופציונלי. אם אין קרדנציאלס זמינים (ENV/Role), האפליקציה תעבוד במצב דמו ותציג נתוני דוגמה.
  - `AWS_DEFAULT_REGION` — אזור עבודה. ברירת מחדל: `us-east-1`.
- רשת: יש לפתוח `5001/TCP` מקומית או ב‑Security Group בעת פריסה.

## מצבי ריצה
- Demo Mode: ללא קרדנציאלס — האפליקציה מציגה נתוני דמו ומוסיפה הודעה בראש הדף. מתאים להרצה "מהקופסה" אצל המרצה.
- Real Mode: עם IAM Role לאינסטנס או עם משתני סביבה של AWS — יוצגו נתונים אמיתיים מ‑AWS.
- Bug Mode (עבור התרגיל): להפעלת שגיאת NameError מכוונת כפי בדרישה, הריצו עם `SHOW_BUG=1`.

## התקנה והרצה

Build:

```bash
docker build -t aws-app .
```

Run — דוגמאות שימוש:

- Demo Mode (ללא קרדנציאלס):
  ```bash
  docker run -p 5001:5001 aws-app
  ```

- Real Mode עם IAM Role על ה‑EC2:
  ```bash
  docker run -p 5001:5001 aws-app
  ```

- Real Mode עם מפתחות (לבדיקה מקומית):
  ```bash
  docker run -p 5001:5001 \
    -e AWS_ACCESS_KEY_ID=YOUR_KEY \
    -e AWS_SECRET_ACCESS_KEY=YOUR_SECRET \
    -e AWS_DEFAULT_REGION=us-east-1 \
    aws-app
  ```

- Bug Mode (שחזור NameError לפי התרגיל):
  ```bash
  docker run -p 5001:5001 -e SHOW_BUG=1 aws-app
  ```

הערה: ב‑Windows PowerShell משתמשים בתחביר `$Env:VAR` להעברת משתני סביבה (למשל `$Env:AWS_ACCESS_KEY_ID`).

בדיקה: גלישה אל `http://localhost:5001/` או `http://<EC2_PUBLIC_IP>:5001/`. במצב דמו תופיע הודעה צהובה בראש הדף.

## פריסה ל-EC2
1. התחברות: `ssh ec2-user@<EC2_PUBLIC_IP>`.
2. שכפול: `git clone <REPO_URL> && cd <REPO_FOLDER>`.
3. מתוך תיקיית הפרויקט, הריצו את הפקודות שבסעיף [התקנה והרצה](#התקנה-והרצה) (Build ואז Run).
4. ודאו פתיחת `5001/TCP` ב‑Security Group.

## ארכיטקטורה ותרשימי זרימה

בקשת משתמש → רינדור נתוני AWS (Sequence):

```mermaid
sequenceDiagram
    participant U as User
    participant B as Browser
    participant F as Flask App
    participant B3 as boto3
    participant AWS as AWS APIs

    U->>B: Open http://host:5001/
    B->>F: HTTP GET /
    F->>B3: describe_instances / describe_vpcs / describe_load_balancers / describe_images
    B3->>AWS: Signed requests (us-east-1)
    AWS-->>B3: JSON responses
    B3-->>F: Dicts
    F-->>B: HTML tables
    B-->>U: Rendered page
```

צינור Build & Deploy (GitHub → EC2 → Docker):

```mermaid
flowchart LR
    Dev[git push] --> EC2[SSH to EC2]
    EC2 --> Clone[git clone]
    Clone --> Build[docker build -t aws-app .]
    Build --> Run[docker run -p 5001:5001 -e AWS_* aws-app]
    Run --> Browser[Open http://<EC2_IP>:5001/]
    SG[Security Group allows TCP 5001] --- Browser
```

## Docker Build (Multi-Stage)

תהליך הבנייה בנוי משלושה שלבים לשמירה על Runtime קטן, מהיר וידידותי לקאשינג:

- base: סביבת בנייה מינימלית
  - `FROM python:<VERSION>-slim`
  - `ENV` (ברירות מחדל נוחות לפייתון)
  - `WORKDIR /app`
  - `apt-get install build-essential` (למקרה שתלויות עקיפות דורשות קומפילציה)

- builder: בניית חבילות התלויות ל‑wheels
  - `COPY requirements.txt .`
  - `pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt`

- runtime: שכבת ריצה רזה בלבד
  - `FROM python:<VERSION>-slim`
  - `ENV` + `WORKDIR /app`
  - `COPY --from=builder /wheels /wheels`
  - `pip install --no-cache-dir /wheels/*`
  - `COPY app.py ./`
  - `EXPOSE 5001`
  - `CMD ["python", "app.py"]`

יתרונות עיקריים:
- אימג' קטן: ללא כלי build בשכבת הריצה.
- התקנה מהירה ודטרמיניסטית: התקנה מ‑wheels מקומיים במקום מהרשת.
- קאשינג יעיל: שינוי בקוד האפליקציה לא מרסק שכבות תלויות.

טיפ: לשינוי גרסת פייתון בזמן build ניתן להשתמש ב‑`--build-arg PYTHON_VERSION=3.12`.

## פתרון בעיות
- Unable to locate credentials: בגרסה זו, ללא קרדנציאלס האפליקציה עוברת אוטומטית ל‑Demo Mode. אם עדיין אתם רואים את ההודעה בתוך הטבלאות, בנו את האימג' מחדש עם `--no-cache` והרימו קונטיינר חדש.
- Region: השתמשו ב‑`AWS_DEFAULT_REGION` (ברירת מחדל `us-east-1`).
- Permissions: עבור Real Mode ודאו של‑IAM Role/Keys יש `ec2:Describe*` ו‑`elasticloadbalancing:Describe*`.
- Port/SG: ודאו פתיחת `5001/TCP` מהרשת הרלוונטית.
- שחזור הבאג לתרגיל: הריצו עם `SHOW_BUG=1` כדי לקבל NameError על `vpcs`.

## קרדיט
הפרויקט נכתב על ידי תלמיד מכללת JB בשם Alex-Y.
