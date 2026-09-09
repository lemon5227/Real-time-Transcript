FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5001

WORKDIR /app

COPY requirements-core.txt requirements-cloud.txt ./
RUN python -m pip install --no-cache-dir -r requirements-cloud.txt \
    && python -m pip install --no-cache-dir "gunicorn>=22,<24"

COPY app.py ./
COPY backend ./backend
COPY static ./static
COPY templates ./templates

EXPOSE 5001

CMD ["sh", "-c", "exec gunicorn --worker-class gthread --workers 1 --threads 100 --timeout 0 --bind 0.0.0.0:${PORT:-5001} app:app"]
