# Telemetry frame-recovery API - pure Python standard library, no deps.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TELEMETRY_PORT=8080

WORKDIR /app

# Application and verification assets.
COPY app/ ./app/
COPY tests/ ./tests/
COPY smoke.py verify.py ./

# Non-root runtime user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('TELEMETRY_PORT','8080')+'/healthz',timeout=2)"

CMD ["python3", "-m", "app.server"]
