# syntax=docker/dockerfile:1

# Dockerfile for the AWS Flask demo application
# מטרות הקובץ:
# - לבנות Image לפייתון ולהתקין בו את התלויות מתוך requirements.txt
# - להשתמש ב-Multi-stage build כדי להשאיר Runtime קטן ומהיר
# - לחשוף פורט 5001 (האפליקציה מאזינה שם)
# - לקבל פרטי גישה ל-AWS כמשתני סביבה בזמן הרצה (לא בזמן build)
#
# שימוש לדוגמה:
#   Build: docker build -t aws-app .
#   Run (עם קרדנציאלס):
#     docker run -p 5001:5001 -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... aws-app

# מאפשר להחליף גרסת פייתון בזמן build: --build-arg PYTHON_VERSION=3.12
ARG PYTHON_VERSION=3.11

# שלב בסיס: מכיל Python וכלים מינימליים הנדרשים לבניית חבילות (wheels)
FROM python:${PYTHON_VERSION}-slim AS base

# הגדרות לשיפור ביצועים וניקיון לוגים: לא ליצור קבצי .pyc ופלט ללא באפרים
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ספריית עבודה בתוך הקונטיינר
WORKDIR /app

# התקנת חבילות מערכת הנדרשות לקומפילציה של חלק מהתלויות (לרוב Flask/boto3 טהורות,
# אך תלויות עקיפות לעיתים דורשות build tools). שלב זה נשאר רק בבילד, לא ברנטיים.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# שלב Builder: פותר ומרכיב את כל התלויות ל-wheels כדי לאפשר התקנה מהירה ודטרמיניסטית
FROM base AS builder

# העתקת קובץ התלויות בלבד כדי לאפשר caching של שכבות
COPY requirements.txt .

# שדרוג pip ובניית wheels לכל התלויות אל /wheels
RUN pip install --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# שלב Runtime: אימג' מינימלי עם פייתון בלבד, ללא כלי build
FROM python:${PYTHON_VERSION}-slim AS runtime

# אותן הגדרות פייתון גם בזמן ריצה
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ספריית עבודה לאפליקציה
WORKDIR /app

# העתקת ה-wheels מה-Builder והתקנתם ללא גישה לרשת (מהיר וחסכוני)
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*

# העתקת קוד האפליקציה
COPY app.py ./
COPY templates ./templates
COPY static ./static

# תיעוד הפורט שאותו נחשוף מהקונטיינר
EXPOSE 5001

# נקודת הכניסה: הפעלת שרת Flask (לצורכי דמו/פיתוח). לפרודקשן עדיף gunicorn.
CMD ["python", "app.py"]
