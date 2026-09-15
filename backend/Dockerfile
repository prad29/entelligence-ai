FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps
COPY backend/. .
# dqscan (repo-root standalone tool) + its config -- see
# app/tasks/dqscan_task.py and backend/Dockerfile.prod's comments.
COPY dqscan ./dqscan
COPY config.yaml config.prod.yaml ./
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
