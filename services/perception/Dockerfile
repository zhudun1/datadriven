# 业务感知服务
FROM python:3.9-slim
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（包含深度学习库）
COPY requirements.txt .
RUN pip install --no-cache-dir torch torchvision timm pymysql redis pointnet2_ops

# 从项目根目录复制需要的代码文件
COPY services/perception_service.py .
COPY business_perception/ ./business_perception/
COPY common/ ./common/
COPY db_config.py .
COPY utils/ ./utils/

# 复制模型文件
COPY checkpoints/ ./checkpoints/

# 启动服务
CMD ["python", "perception_service.py"]