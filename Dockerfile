FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Yekaterinburg

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY VERSION ./VERSION

# uid 1000 совпадает с владельцем ./data на хосте, иначе bind-mount будет недоступен на запись.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin watchdog \
    && mkdir -p /data/torrents \
    && chown -R watchdog:watchdog /data /app

USER watchdog

EXPOSE 8096

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${APP_PORT:-8096}/health" > /dev/null || exit 1

# Ровно один воркер — обязательное условие, а не упущение: APScheduler живёт внутри
# процесса, и каждый лишний воркер даст ещё одну ежедневную проверку, то есть
# параллельные скачивания одного торрента и гонку при применении обновления.
CMD ["sh", "-c", "uvicorn app.main:app --host ${APP_HOST:-0.0.0.0} --port ${APP_PORT:-8096} --workers 1"]
