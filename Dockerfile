# syntax=docker/dockerfile:1
# Small, non-root image for linux/amd64 and linux/arm64 (Raspberry Pi).
FROM python:3.12-alpine

LABEL org.opencontainers.image.title="panda-print" \
      org.opencontainers.image.description="Self-hosted dashboard for a home printer" \
      org.opencontainers.image.source="https://github.com/tusharkaurani/panda-print"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# uid 1000 matches the usual first user on a Raspberry Pi, so bind-mounted
# folders owned by that user are readable (and writable, for ./data).
RUN adduser -D -u 1000 panda \
 && mkdir -p /data /config /host-state \
 && chown panda /data

COPY app ./app
# starter launcher config, used when /config has no links.yml
COPY config/links.yml ./defaults/links.yml
RUN python -m compileall -q app

ENV PORT=8095 \
    DATA_DIR=/data \
    CONFIG_DIR=/config \
    HOST_STATE_FILE=/host-state/state

USER 1000:1000
VOLUME ["/data"]
EXPOSE 8095

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-m", "app.healthcheck"]

CMD ["python", "-m", "app"]
