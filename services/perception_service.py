"""
业务感知微服务
从感知队列消费请求，处理后发送到编排队列
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 环境变量支持 Docker 部署
MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "qos_app")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "QosApp@123")

# CUDA 环境变量（Windows）- 必须在 import torch 之前设置
# pointnet2_ops 库会检查 CUDA_HOME，不管 PyTorch 是否可用 CUDA
os.environ["CUDA_HOME"] = "C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v11.7"
os.environ["CUDA_VISIBLE_DEVICES"] = ""  # 禁用 CUDA 设备，强制使用 CPU

import torch
import numpy as np

from common.message_queue import MessageQueue, get_message_queue
from common.utils import init_logger

logger = init_logger()

CLASS_NAME = "cable_gland"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKPOINT_PATH = os.path.join(BASE_DIR, "checkpoints", "checkpoints_CFM_mvtec")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


def load_rgb_pc(rgb_path: str, pcd_path: str):
    """加载用户输入的图像和点云文件，与 TestDataset 处理方式一致"""
    from PIL import Image
    from torchvision import transforms
    from utils.mvtec3d_utils import (
        read_tiff_organized_pc,
        organized_pc_to_depth_map,
        resize_organized_pc,
    )
    from utils.general_utils import SquarePad

    IMAGENET_MEAN = [0.485, 0.456, 0.406]
    IMAGENET_STD = [0.229, 0.224, 0.225]
    RGB_SIZE = 224

    img = Image.open(rgb_path).convert("RGB")
    img = transforms.Compose([
        SquarePad(),
        transforms.Resize((RGB_SIZE, RGB_SIZE), interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.ToTensor(),
        transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
    ])(img)

    organized_pc = read_tiff_organized_pc(pcd_path)
    depth_map_3ch = np.repeat(organized_pc_to_depth_map(organized_pc)[:, :, np.newaxis], 3, axis=2)
    resized_organized_pc = resize_organized_pc(organized_pc, target_height=RGB_SIZE, target_width=RGB_SIZE)

    return img.unsqueeze(0), resized_organized_pc.unsqueeze(0)


def write_perception_result(data_id: int, anomaly_score: float, risk_level: str, qos_vector: list):
    """将感知结果写入 business_awareness 数据库"""
    import pymysql
    from datetime import datetime

    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": "business_awareness", "charset": "utf8mb4"
    }
    conn = pymysql.connect(**conn_cfg)
    with conn.cursor() as c:
        c.execute(
            "INSERT INTO t_perception_log (data_id, anomaly_score, risk_level, qos_vector, create_time) "
            "VALUES (%s, %s, %s, %s, %s)",
            (data_id, float(anomaly_score), risk_level, str(qos_vector), datetime.now())
        )
        conn.commit()
    conn.close()


class PerceptionService:
    def __init__(self):
        self.mq = get_message_queue()
        # 延迟加载模型，避免启动时就触发 CUDA 环境检查
        self.detector = None
        self.translator = None
        self._ensure_perception_log_table()
        logger.info("业务感知服务初始化完成")

    def _ensure_models(self):
        """延迟加载模型，尝试 CPU 模式"""
        if self.detector is None:
            try:
                # 尝试导入并加载模型（CPU 模式）
                from business_perception.cfm_detector import CFMDetector
                from business_perception.qos_translator import QoSTranslator
                # 强制使用 CPU
                self.detector = CFMDetector(CLASS_NAME, CHECKPOINT_PATH, "cpu")
                self.translator = QoSTranslator()
                logger.info("模型加载完成（CPU模式）")
            except Exception as e:
                logger.error(f"模型加载失败: {e}")
                # 启用降级模式
                self.detector = None
                self.translator = None
                logger.warning("感知服务降级为仅传递消息模式")

    def _ensure_perception_log_table(self):
        """确保感知日志表存在"""
        import pymysql
        conn_cfg = {
            "host": MYSQL_HOST, "port": MYSQL_PORT,
            "user": MYSQL_USER, "password": MYSQL_PASSWORD,
            "database": "business_awareness", "charset": "utf8mb4"
        }
        conn = pymysql.connect(**conn_cfg)
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS t_perception_log (
                    log_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    data_id BIGINT,
                    anomaly_score FLOAT DEFAULT 0,
                    risk_level VARCHAR(32) DEFAULT '',
                    qos_vector TEXT,
                    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        conn.close()

    def process(self, message: dict) -> dict:
        # 确保模型已加载
        self._ensure_models()

        task_id = message.get("task_id", "unknown")
        data_id = message.get("data_id", 0)
        rgb_path = message.get("rgb_path", "")
        pcd_path = message.get("pcd_path", "")

        logger.info(f"[{task_id}] 开始处理: {rgb_path}")

        try:
            # 检查是否为降级模式
            if self.detector is None:
                # 降级模式：跳过模型推理，直接使用默认 QoS 向量
                logger.warning(f"[{task_id}] 感知服务降级模式，使用默认 QoS 向量")
                anomaly_score = 0.5
                risk_level = "medium"
                # 编排服务期望 4 个元素: 带宽, 延迟, 优先级, 丢包率
                qos_list = [10.0, 5.0, 1, 0.01]
            else:
                # 正常模式：加载数据并推理
                # 1. 加载数据
                rgb, pc = load_rgb_pc(rgb_path, pcd_path)

                # 2. 异常检测
                anomaly_score = self.detector.get_anomaly_score(rgb, pc)
                logger.info(f"[{task_id}] 异常分数: {anomaly_score:.4f}")

                # 3. QoS翻译
                risk_level, qos_vector = self.translator.translate(float(anomaly_score))
                qos_list = qos_vector.round(4).tolist()
                logger.info(f"[{task_id}] 风险等级: {risk_level}, QoS向量: {qos_list}")

            # 4. 写数据库（business_awareness）
            write_perception_result(data_id, anomaly_score, risk_level, qos_list)

            # 5. 发消息到编排队列
            orch_message = {
                "task_id": task_id,
                "data_id": data_id,
                "anomaly_score": round(float(anomaly_score), 4),
                "risk_level": risk_level,
                "qos_vector": qos_list,
                "resource_request": message.get("resource_request", {})
            }
            self.mq.publish(MessageQueue.QUEUE_ORCHESTRATION, orch_message)
            logger.info(f"[{task_id}] 已发送到编排队列")

            return {"status": "success", "task_id": task_id}

        except Exception as e:
            logger.error(f"[{task_id}] 处理失败: {str(e)}", exc_info=True)
            return {"status": "error", "task_id": task_id, "error": str(e)}

    def run(self):
        logger.info("业务感知服务启动，等待消息...")

        while True:
            try:
                message = self.mq.consume(MessageQueue.QUEUE_PERCEPTION, blocking=True, timeout=30)
                if message:
                    self.process(message)
            except KeyboardInterrupt:
                logger.info("收到中断信号，停止服务")
                break
            except Exception as e:
                logger.error(f"服务异常: {str(e)}", exc_info=True)


def main():
    logger.info("=" * 30)
    logger.info("启动业务感知微服务")
    logger.info("=" * 30)
    service = PerceptionService()
    service.run()


if __name__ == "__main__":
    main()