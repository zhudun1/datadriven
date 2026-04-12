# API 网关服务
FROM python:3.9-slim
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi uvicorn pymysql redis

# 从项目根目录复制需要的代码文件
COPY services/gateway.py .
COPY common/ ./common/
COPY db_config.py .

# 暴露端口
EXPOSE 8001

# 启动服务
CMD ["python", "gateway.py"]