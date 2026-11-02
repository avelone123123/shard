FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends g++ make git curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir transformers accelerate safetensors huggingface_hub
COPY pyproject.toml setup.py README.md ./
COPY native ./native
RUN g++ -O3 -mavx2 -mfma -shared -fPIC native/shard_core.cpp -o shard_core.so
COPY . .
RUN pip install --no-cache-dir -e .
ENV SHARD_HOST=0.0.0.0
ENV SHARD_PORT=8000
ENV SHARD_DOCKER=1
EXPOSE 8000
CMD ["python", "main.py"]