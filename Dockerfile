FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends g++ make git curl && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml setup.py README.md ./
COPY native ./native
COPY . .
RUN pip install --no-cache-dir -e .
ENV SHARD_MODELS_DIR=/data/models
ENV SHARD_DATA_DIR=/data/shard
VOLUME ["/data/models", "/data/shard"]
EXPOSE 8000
CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "8000"]