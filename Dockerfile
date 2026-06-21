FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn[standard] jinja2 pymysql cryptography

COPY app /app/app

CMD ["sh", "-lc", "uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT:-8050}"]
