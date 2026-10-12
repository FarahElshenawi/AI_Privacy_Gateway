FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# SQLite (default) needs no setup. With PostgreSQL, run `alembic upgrade head` once per release before starting.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
