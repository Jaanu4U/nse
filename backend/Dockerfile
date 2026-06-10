FROM python:3.11-slim

WORKDIR /workspace

# Install system dependencies needed for compiling packages
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

CMD ["python", "run.py"]
