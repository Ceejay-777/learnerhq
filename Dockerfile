FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    DATABASE_URL=postgresql://dummy:dummy@localhost:5432/dummy \
    DJANGO_SECRET_KEY=build-time-dummy-key

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput

CMD python manage.py migrate --noinput && python manage.py collectstatic --noinput && uvicorn config.asgi:application --host 0.0.0.0 --port $PORT --workers 4 --timeout-keep-alive 75 --timeout-graceful-shutdown 15

