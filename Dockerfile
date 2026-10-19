FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . /app

RUN mkdir -p /app/paper_live /app/ws_live /app/dashboard

EXPOSE 8765

CMD ["python", "serve_dashboard.py", "--host", "0.0.0.0", "--port", "8765"]
