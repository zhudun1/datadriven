import numpy as np
import logging

# 初始化日志
def init_logger():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(module)s - %(levelname)s - %(message)s"
    )
    return logging.getLogger(__name__)

# 数据归一化到0-1
def normalize(data: np.ndarray, min_val: float, max_val: float) -> np.ndarray:
    return (data - min_val) / (max_val - min_val + 1e-8)  # 避免除0

# 计算资源碎片率（资源分配后未使用的小碎片占比）
def calculate_resource_fragmentation(
    node_resources: np.ndarray,  # 节点剩余资源
    used_resources: np.ndarray   # 已使用资源
) -> float:
    total_resources = node_resources + used_resources
    # 碎片 = 剩余资源 < 最小需求（0.05）的部分
    fragmented = np.sum(node_resources[node_resources < 0.05])
    total = np.sum(total_resources)
    return fragmented / (total + 1e-8)