"""
业务感知微服务
从感知队列消费请求，处理后发送到编排队列
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 添加CFM模型需要的路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "models"))

# 环境变量支持本地/云端部署
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

    def _ensure_models(self, model_type: str = "CFM"):
        """延迟加载模型，根据model_type加载对应的检测器"""
        key = f"{model_type}_loaded"

        # 检查是否已加载该模型
        if hasattr(self, key) and getattr(self, key):
            return

        try:
            from business_perception.qos_translator import QoSTranslatorV3 as QoSTranslator
        except ImportError:
            try:
                from business_perception.qos_translator import QoSTranslatorV2 as QoSTranslator
            except ImportError:
                try:
                    from business_perception.qos_translator import QoSTranslator as QoSTranslator
                except ImportError:
                    QoSTranslator = None

        try:
            if model_type == "CFM":
                from business_perception.cfm_detector import CFMDetector
                self.detector = CFMDetector(CLASS_NAME, CHECKPOINT_PATH, "cpu")
                self.translator = QoSTranslator() if QoSTranslator else None
                self.cfm_loaded = True
                logger.info("CFM模型加载完成（CPU模式）")
            elif model_type == "Jigsaw":
                # 动态加载Jigsaw检测器
                import importlib.util
                import os
                BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                jigsaw_detector_path = os.path.join(BASE_DIR, "business_perception", "jigsaw_detector.py")
                if os.path.exists(jigsaw_detector_path):
                    spec = importlib.util.spec_from_file_location("jigsaw_module", jigsaw_detector_path)
                    jigsaw_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(jigsaw_module)
                    JigsawVADDetector = jigsaw_module.JigsawVADDetector

                    # 获取Jigsaw模型路径
                    jigsaw_base = os.path.join(BASE_DIR, "Jigsaw-VAD-main", "checkpoints")
                    jigsaw_checkpoint = os.path.join(jigsaw_base, "avenue_92.18.pth")
                    if not os.path.exists(jigsaw_checkpoint):
                        # 查找其他可能的路径
                        jigsaw_base = os.path.join(BASE_DIR, "checkpoints")
                        for f in os.listdir(jigsaw_base):
                            if f.endswith('.pth'):
                                jigsaw_checkpoint = os.path.join(jigsaw_base, f)
                                break

                    self.jigsaw_detector = JigsawVADDetector(
                        checkpoint_path=jigsaw_checkpoint,
                        time_length=7,
                        device="cpu"
                    )
                    self.jigsaw_translator = QoSTranslator() if QoSTranslator else None
                    self.jigsaw_loaded = True
                    logger.info(f"Jigsaw模型加载完成: {jigsaw_checkpoint}")
                else:
                    logger.warning("Jigsaw检测器文件不存在")
        except Exception as e:
            logger.error(f"模型加载失败 [{model_type}]: {e}")
            logger.warning(f"感知服务降级模式 [{model_type}]")

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
        # 获取模型类型
        model_type = message.get("model_type", "CFM")

        # 根据模型类型加载对应模型
        self._ensure_models(model_type)

        task_id = message.get("task_id", "unknown")
        data_id = message.get("data_id", 0)

        # 根据模型类型获取路径
        if model_type == "Jigsaw":
            video_path = message.get("video_path", "")
            aux_image_path = message.get("aux_image_path", "")
        else:
            video_path = ""
            aux_image_path = ""
            rgb_path = message.get("rgb_path", "")
            pcd_path = message.get("pcd_path", "")

        logger.info(f"[{task_id}] 开始处理 [{model_type}]: {video_path or aux_image_path or rgb_path}")

        try:
            # Jigsaw模式
            if model_type == "Jigsaw":
                if hasattr(self, 'jigsaw_detector') and self.jigsaw_detector:
                    # 使用Jigsaw模型进行检测
                    if video_path:
                        anomaly_score = self.jigsaw_detector.get_anomaly_score(video_path=video_path)
                    elif aux_image_path:
                        anomaly_score = self.jigsaw_detector.get_anomaly_score(image_path=aux_image_path)
                    else:
                        anomaly_score = 0.5
                    logger.info(f"[{task_id}] Jigsaw异常分数: {anomaly_score:.4f}")

                    # 使用Jigsaw翻译器
                    if hasattr(self, 'jigsaw_translator') and self.jigsaw_translator:
                        risk_level, qos_vector = self.jigsaw_translator.translate_simple(float(anomaly_score))
                    else:
                        risk_level, qos_vector = self.translator.translate_simple(float(anomaly_score))
                    qos_list = qos_vector.round(4).tolist() if hasattr(qos_vector, 'round') else [0.3, 0.3, 0.3, 0.3]
                    logger.info(f"[{task_id}] 风险等级: {risk_level}, QoS向量: {qos_list}")
                else:
                    # Jigsaw模型未加载，使用降级模式
                    logger.warning(f"[{task_id}] Jigsaw模型未加载，使用默认分数")
                    anomaly_score = 0.5
                    risk_level = "medium"
                    qos_list = [10.0, 5.0, 1, 0.01]
            # CFM模式
            elif self.detector is None:
                # 降级模式
                logger.warning(f"[{task_id}] 感知服务降级模式，使用默认 QoS 向量")
                anomaly_score = 0.5
                risk_level = "medium"
                qos_list = [10.0, 5.0, 1, 0.01]
            else:
                # CFM正常模式
                rgb, pc = load_rgb_pc(rgb_path, pcd_path)
                anomaly_score = self.detector.get_anomaly_score(rgb, pc)
                logger.info(f"[{task_id}] CFM异常分数: {anomaly_score:.4f}")

                risk_level, qos_vector = self.translator.translate_simple(float(anomaly_score))
                qos_list = qos_vector.round(4).tolist()
                logger.info(f"[{task_id}] 风险等级: {risk_level}, QoS向量: {qos_list}")

                # 2. 异常检测
                anomaly_score = self.detector.get_anomaly_score(rgb, pc)
                logger.info(f"[{task_id}] 异常分数: {anomaly_score:.4f}")

                # 3. QoS翻译
                risk_level, qos_vector = self.translator.translate_simple(float(anomaly_score))
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

            # 返回完整结果，包括 anomaly_score 和其他信息
            return {
                "status": "success",
                "task_id": task_id,
                "data_id": data_id,
                "anomaly_score": round(float(anomaly_score), 4),
                "risk_level": risk_level,
                "qos_vector": qos_list,
                "resource_request": message.get("resource_request", {})
            }

        except Exception as e:
            logger.error(f"[{task_id}] 处理失败: {str(e)}", exc_info=True)
            return {"status": "error", "task_id": task_id, "error": str(e)}

    def run(self):
        logger.info("业务感知服务启动，等待消息...")

        while True:
            try:
                # 使用轮询方式，更加可靠
                message = None
                try:
                    # 非阻塞获取
                    message = self.mq.consume(MessageQueue.QUEUE_PERCEPTION, blocking=False)
                except Exception as e:
                    # 如果出错，尝试用 lpop 直接获取
                    data = self.mq.redis_client.lpop(MessageQueue.QUEUE_PERCEPTION)
                    if data:
                        message = self.mq._deserialize(data)

                if message:
                    logger.info(f"收到消息: task_id={message.get('task_id')}")
                    result = self.process(message)
                    logger.info(f"处理结果: {result.get('status')}, anomaly={result.get('anomaly_score', 'N/A')}")
                    if result.get('status') == 'error':
                        logger.error(f"处理失败: {result.get('error')}")
                else:
                    # 没有消息时短暂等待
                    import time
                    time.sleep(0.5)
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