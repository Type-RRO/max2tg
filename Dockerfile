FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=100 -i https://mirror.yandex.ru/pypi/simple -r requirements.txt

COPY . .

CMD ["python", "-m", "app.main"]
