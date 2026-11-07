FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    BOOK_DIR=/books \
    DATA_DIR=/data \
    PORT=5000

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .
COPY ktphane ./ktphane

VOLUME ["/data"]
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/health'%os.environ.get('PORT','5000'),timeout=4)" || exit 1

# Tek worker: tarayıcı (scanner) ve önbellek tek süreçte kalır; eşzamanlılık thread'lerle sağlanır.
CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT} app:app"]
