FROM python:3.12-slim

WORKDIR /srv/bootlegger
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY hands ./hands
COPY pyproject.toml .

ENV BOOTLEGGER_DB=/data/bootlegger.db \
    BOOTLEGGER_AUDIT_DIR=/data/audit

EXPOSE 8484
CMD ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8484"]
