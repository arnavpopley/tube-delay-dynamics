FROM python:3.12-slim

# Collector is stdlib-only. Image exists so a VPS can run
#   docker compose up -d
# with restart-on-failure, same contract as the systemd unit.

WORKDIR /app

COPY collector/ /app/collector/
COPY PROJECT.md /app/PROJECT.md

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    POLL_INTERVAL_SECONDS=30 \
    HEALTH_STALE_SECONDS=600 \
    LOG_LEVEL=INFO

VOLUME ["/app/data"]

# Restart policy belongs in compose/systemd, not the image.
CMD ["python", "/app/collector/tfl_collector.py"]
