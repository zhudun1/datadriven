# 智能编排服务
FROM python:3.9-slim
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（包含深度学习库）
COPY requirements.txt .
RUN pip install --no-cache-dir torch torchvision stable-baselines3 pymysql redis

# 从项目根目录复制需要的代码文件
COPY services/orchestration_service.py .
COPY intelligent_orchestration/ ./intelligent_orchestration/
COPY common/ ./common/
COPY db_config.py .
COPY models/ ./models/

# 复制PPO模型（如果存在）
COPY ppo_graph_model.zip . 2>/dev/null || true

# 启动服务
CMD ["python", "orchestration_service.py"]