FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY data ./data
COPY api ./api
COPY mcp ./mcp
COPY registry ./registry
COPY rag ./rag
COPY skills ./skills
COPY workflow_native ./workflow_native
COPY world ./world

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
