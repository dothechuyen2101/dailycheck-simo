FROM python:3.11-slim

LABEL maintainer="SIMO – Phòng Phần mềm"
LABEL description="SIMO Automated Daily Check System"

WORKDIR /app

# Cài dependencies hệ thống (cho paramiko)
# tzdata: để TZ=Asia/Ho_Chi_Minh có hiệu lực, nếu thiếu thì giờ trong báo cáo là UTC
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    openssh-client \
    fonts-dejavu-core \
    tzdata \
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
