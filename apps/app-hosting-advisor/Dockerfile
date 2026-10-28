FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY apps/app-hosting-advisor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/app-hosting-advisor/app ./app
COPY compositions ./compositions

USER 65532:65532

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
