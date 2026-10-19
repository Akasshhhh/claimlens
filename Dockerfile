FROM python:3.12.7-slim

WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN python -m pip install --no-cache-dir --upgrade pip==25.0.1 \
    && python -m pip install --no-cache-dir -r requirements.lock
COPY . .
CMD ["python", "-m", "uvicorn", "claimlens.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
