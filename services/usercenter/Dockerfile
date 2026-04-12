# 用户中心服务
FROM python:3.9-slim
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装基础依赖
COPY requirements.txt .
RUN pip install --no-cache-dir pymysql

# 从项目根目录复制需要的代码文件
COPY backend_server.py .
COPY db_config.py .
COPY common/ ./common/
COPY frontend/ ./frontend/

# 暴露端口
EXPOSE 8003

# 启动服务
CMD ["python", "-c", "
import os
os.chdir('frontend')
from http.server import HTTPServer
from backend_server import Handler
print('用户中心服务: http://localhost:8003')
HTTPServer(('0.0.0.0', 8003), Handler).serve_forever()
"]