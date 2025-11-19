# استخدم صورة بايثون أساسية
FROM python:3.11-slim

# تعيين متغيرات البيئة
ENV PYTHONUNBUFFERED 1
ENV APP_HOME /app
WORKDIR $APP_HOME

# تثبيت التبعيات اللازمة لـ pdfplumber و docx (مثل libxml2-dev و libxslt-dev)
# وإزالة حزمة spacy غير المستخدمة
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt-dev \
    libmagic1 \
    tesseract-ocr \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

# نسخ ملف requirements.txt وتثبيت التبعيات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات التطبيق
COPY . $APP_HOME

EXPOSE 8000

# تشغيل التطبيق باستخدام Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
