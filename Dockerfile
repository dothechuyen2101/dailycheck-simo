FROM python:3.11-slim

LABEL maintainer="SIMO – Phòng Phần mềm"
LABEL description="SIMO Automated Daily Check System"

WORKDIR /app

# Cài dependencies hệ thống (cho paramiko)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Cài Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Tạo thư mục runtime
RUN mkdir -p data/reports logs

# Port dashboard
EXPOSE 5000

# Mặc định: chạy cả scheduler và dashboard
CMD ["python", "main.py"]
