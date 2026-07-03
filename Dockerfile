FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       iputils-ping \
       snmp \
       curl \
       rrdtool \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn[standard] jinja2 pymysql cryptography requests

COPY app /app/app

CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8051"]
