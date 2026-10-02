FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NOTIPER_DATA_DIR=/data \
    NOTIPER_PORT=12353

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY notiper ./notiper

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /data

# The entrypoint fixes /data ownership and drops root privileges to PUID:PGID.
ENV PUID=1000 \
    PGID=1000

VOLUME ["/data"]
EXPOSE 12353

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"NOTIPER_PORT\"]}/healthz', timeout=4)"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "notiper"]
