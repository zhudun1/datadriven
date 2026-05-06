"""
QoS翻译器 - 三层设计版本
基于: 传输需求层 + 业务语义层 → 6维QoS向量

设计思路:
  第一层(传输需求): 数据量 → 带宽需求、时延预算
  第二层(业务语义): 异常等级 + 业务场景 → 优先级、可靠性
  第三层(综合输出): 6维QoS向量

6维QoS向量:
  1. latency_budget (ms) - 端到端时延上限
  2. jitter_bound (ms) - 时延抖动上限
  3. loss_tolerance (%) - 丢包率上限
  4. throughput_required (Mbps) - 最低吞吐量
  5. reliability_class - 可靠性等级 (2=尽力而为,3=中,4=高,5=极高)
  6. priority - 调度优先级 (0-7)
"""
import numpy as np
from typing import Dict, List, Tuple, Optional


class QoSTranslatorV3:
    """三层设计QoS翻译器"""

    # ==================== 第一层: 异常等级定义 ====================
    ANOMALY_LEVELS = {
        "critical": {"score_range": (0.75, 1.0), "name": "紧急安全", "jitter_ratio": 0.1},
        "high": {"score_range": (0.5, 0.75), "name": "关键缺陷", "jitter_ratio": 0.1},
        "medium": {"score_range": (0.3, 0.5), "name": "微小外观瑕疵", "jitter_ratio": 0.2},
        "normal": {"score_range": (0.0, 0.3), "name": "正常/监控", "jitter_ratio": 0.2},
    }

    # ==================== 第二层: 业务场景定义 ====================
    BUSINESS_SCENARIOS = {
        "safety": {"name": "安全监控", "critical": True},
        "quality": {"name": "质量检测", "critical": True},
        "maintenance": {"name": "巡检维护", "critical": False},
        "monitoring": {"name": "周期性监控", "critical": False},
    }

    # ==================== 第二层: 异常等级+业务场景 → (时延预算, 可靠性等级, 优先级) ====================
    # 按用户提供的表格设计
    BASE_RULES = {
        # critical 级别
        ("critical", "safety"): {"latency_budget_ms": 50, "reliability_class": 5, "priority": 7},
        ("critical", "quality"): {"latency_budget_ms": 50, "reliability_class": 5, "priority": 7},
        ("critical", "maintenance"): {"latency_budget_ms": 100, "reliability_class": 4, "priority": 7},
        ("critical", "monitoring"): {"latency_budget_ms": 100, "reliability_class": 4, "priority": 7},

        # high 级别
        ("high", "safety"): {"latency_budget_ms": 200, "reliability_class": 4, "priority": 6},
        ("high", "quality"): {"latency_budget_ms": 200, "reliability_class": 4, "priority": 6},
        ("high", "maintenance"): {"latency_budget_ms": 300, "reliability_class": 3, "priority": 5},
        ("high", "monitoring"): {"latency_budget_ms": 500, "reliability_class": 3, "priority": 4},

        # medium 级别
        ("medium", "safety"): {"latency_budget_ms": 1000, "reliability_class": 3, "priority": 4},
        ("medium", "quality"): {"latency_budget_ms": 1000, "reliability_class": 3, "priority": 4},
        ("medium", "maintenance"): {"latency_budget_ms": 2000, "reliability_class": 3, "priority": 3},
        ("medium", "monitoring"): {"latency_budget_ms": 3000, "reliability_class": 2, "priority": 2},

        # normal 级别
        ("normal", "safety"): {"latency_budget_ms": 5000, "reliability_class": 3, "priority": 2},
        ("normal", "quality"): {"latency_budget_ms": 5000, "reliability_class": 3, "priority": 2},
        ("normal", "maintenance"): {"latency_budget_ms": 8000, "reliability_class": 2, "priority": 1},
        ("normal", "monitoring"): {"latency_budget_ms": 5000, "reliability_class": 2, "priority": 2},
    }

    # 可靠性等级 → 丢包率映射
    LOSS_TOLERANCE_MAP = {
        5: 0.01,    # 极高: 0.01%
        4: 0.1,     # 高: 0.1%
        3: 0.5,     # 中: 0.5%
        2: 1.0,     # 尽力而为: 1%
    }

    # 可靠性等级名称
    RELIABILITY_NAMES = {
        5: "极高(5个9)",
        4: "高(4个9)",
        3: "中(3个9)",
        2: "尽力而为",
    }

    # 归一化参数
    NORMALIZE_PARAMS = {
        "max_latency_ms": 10000.0,
        "max_jitter_ms": 2000.0,
        "max_loss_rate": 1.0,
        "max_throughput_mbps": 1000.0,
        "max_priority": 7.0,
    }

    def __init__(self):
        """初始化QoS翻译器"""
        self._ensure_default_rules()

    def _ensure_default_rules(self):
        """确保所有组合有默认规则"""
        for level_name in self.ANOMALY_LEVELS.keys():
            for scenario_name in self.BUSINESS_SCENARIOS.keys():
                key = (level_name, scenario_name)
                if key not in self.BASE_RULES:
                    # 默认规则
                    self.BASE_RULES[key] = {
                        "latency_budget_ms": 1000,
                        "reliability_class": 3,
                        "priority": 3,
                    }

    # ==================== 第一层: 传输需求计算 ====================
    def calculate_throughput(self, data_size_mb: float, latency_budget_ms: float, redundancy: float = 1.2) -> float:
        """计算所需吞吐量

        Args:
            data_size_mb: 数据大小(MB)
            latency_budget_ms: 时延预算(ms)
            redundancy: 冗余因子

        Returns:
            throughput_mbps: 所需吞吐量(Mbps)
        """
        if latency_budget_ms <= 0:
            return 0.1

        # throughput = data_size / time * redundancy
        time_seconds = latency_budget_ms / 1000.0
        throughput = (data_size_mb / time_seconds) * redundancy

        return max(throughput, 0.1)

    # ==================== 辅助方法 ====================
    def get_anomaly_level(self, anomaly_score: float) -> str:
        """根据异常分数确定异常等级"""
        for level_name, level_info in self.ANOMALY_LEVELS.items():
            min_score, max_score = level_info["score_range"]
            if min_score <= anomaly_score < max_score:
                return level_name
        return "critical"  # 最高级

    def get_business_scenario(self, scenario: str = None) -> str:
        """确定业务场景"""
        if scenario and scenario in self.BUSINESS_SCENARIOS:
            return scenario
        return "monitoring"  # 默认

    def calculate_jitter(self, latency_budget_ms: float, anomaly_level: str) -> float:
        """计算抖动上限"""
        level_info = self.ANOMALY_LEVELS.get(anomaly_level, {})
        jitter_ratio = level_info.get("jitter_ratio", 0.2)
        return latency_budget_ms * jitter_ratio

    def calculate_loss_tolerance(self, reliability_class: int) -> float:
        """计算丢包率"""
        return self.LOSS_TOLERANCE_MAP.get(reliability_class, 0.5)

    # ==================== 第三层: 核心翻译方法 ====================
    def translate(
        self,
        anomaly_score: float,
        data_size_mb: float,
        data_type: str = "image",
        business_scenario: str = None,
    ) -> Tuple[str, np.ndarray, Dict]:
        """核心翻译: 三层设计 → 6维QoS向量

        Args:
            anomaly_score: 异常分数 (0-1)
            data_size_mb: 数据大小(MB)
            data_type: 数据类型 (image/video/pointcloud)
            business_scenario: 业务场景

        Returns:
            anomaly_level: 异常等级
            qos_vector: 6维归一化QoS向量
            qos_details: 详细QoS参数
        """
        # 第一层: 确定异常等级
        anomaly_level = self.get_anomaly_level(anomaly_score)

        # 第二层: 确定业务场景
        scenario = self.get_business_scenario(business_scenario)

        # 第二层: 查表获取基础规则
        rule_key = (anomaly_level, scenario)
        rule = self.BASE_RULES.get(rule_key, {
            "latency_budget_ms": 1000,
            "reliability_class": 3,
            "priority": 3
        })

        latency_budget_ms = rule["latency_budget_ms"]
        reliability_class = rule["reliability_class"]
        priority = rule["priority"]

        # 第一层: 计算吞吐量（数据量决定）
        throughput_mbps = self.calculate_throughput(data_size_mb, latency_budget_ms)

        # 第二层: 计算抖动（异常等级决定）
        jitter_ms = self.calculate_jitter(latency_budget_ms, anomaly_level)

        # 第二层: 计算丢包率（可靠性决定）
        loss_tolerance = self.calculate_loss_tolerance(reliability_class)

        # 第三层: 构建6维QoS向量（归一化）
        qos_vector = np.array([
            latency_budget_ms / self.NORMALIZE_PARAMS["max_latency_ms"],
            jitter_ms / self.NORMALIZE_PARAMS["max_jitter_ms"],
            loss_tolerance / self.NORMALIZE_PARAMS["max_loss_rate"],
            throughput_mbps / self.NORMALIZE_PARAMS["max_throughput_mbps"],
            reliability_class / 5.0,  # 2-5 归一化
            priority / self.NORMALIZE_PARAMS["max_priority"],
        ], dtype=np.float32)

        # 裁剪到[0,1]
        qos_vector = np.clip(qos_vector, 0.0, 1.0)

        # 第三层: 构建详细输出
        qos_details = {
            # 第一层输出
            "data_size_mb": data_size_mb,
            "data_type": data_type,
            "throughput_required_mbps": round(throughput_mbps, 2),
            # 第二层输出
            "anomaly_level": anomaly_level,
            "anomaly_name": self.ANOMALY_LEVELS.get(anomaly_level, {}).get("name", ""),
            "business_scenario": scenario,
            "latency_budget_ms": latency_budget_ms,
            "jitter_bound_ms": round(jitter_ms, 2),
            "loss_tolerance_pct": loss_tolerance,
            "reliability_class": reliability_class,
            "reliability_name": self.RELIABILITY_NAMES.get(reliability_class, "中"),
            "priority": priority,
        }

        return anomaly_level, qos_vector, qos_details

    def translate_simple(self, anomaly_score: float, data_size_mb: float = 1.0) -> Tuple[str, np.ndarray]:
        """简化接口"""
        level, qos_vector, _ = self.translate(anomaly_score, data_size_mb)
        return level, qos_vector

    # ==================== 查看接口 ====================
    def get_base_rules_table(self) -> Dict:
        """获取基规则表"""
        table = {}
        for (level, scenario), rule in self.BASE_RULES.items():
            key = f"{level}_{scenario}"
            reliability = self.RELIABILITY_NAMES.get(rule["reliability_class"], "中")

            table[key] = {
                "anomaly_level": level,
                "anomaly_name": self.ANOMALY_LEVELS.get(level, {}).get("name", ""),
                "business_scenario": scenario,
                "latency_budget_ms": rule["latency_budget_ms"],
                "reliability_class": rule["reliability_class"],
                "reliability": reliability,
                "priority": rule["priority"],
            }
        return table

    def get_qos_profiles(self) -> Dict:
        """获取QoS配置剖面"""
        profiles = {}

        test_cases = [
            (0.85, 50.0, "quality"),      # critical: 高分+大数据+质量检测
            (0.65, 50.0, "quality"),      # high: 中高分+大数据+质量检测
            (0.40, 0.1, "monitoring"), # medium: 低分+小数据+监控
            (0.15, 0.1, "monitoring"), # normal: 正常+小数据+监控
        ]

        for anomaly_score, data_size, scenario in test_cases:
            level, qos_vector, details = self.translate(
                anomaly_score,
                data_size_mb=data_size,
                business_scenario=scenario
            )
            key = f"{level}_{scenario}"
            profiles[key] = {
                "anomaly_score": anomaly_score,
                "data_size_mb": data_size,
                "qos_vector": qos_vector.tolist(),
                "qos_details": details,
            }

        return profiles


# ==================== 数据驱动学习接口(预留) ====================
class AdaptiveQoSLearning:
    """数据驱动QoS优化学习器

    收集历史数据:
    - 输入: (异常等级, 数据量, 分配的QoS向量)
    - 输出: (实际时延, 实际丢包, 检测准确率)

    用MLP拟合:
    - (QoS向量) → 检测准确率

    反向优化:
    - 给定目标准确率 → 搜索满足条件的最小QoS
    """

    def __init__(self):
        self.history_data = []
        self.model = None

    def record_sample(
        self,
        anomaly_level: str,
        data_size_mb: float,
        qos_vector: np.ndarray,
        actual_latency_ms: float,
        actual_loss_pct: float,
        detection_accuracy: float
    ):
        """记录样本"""
        sample = {
            "anomaly_level": anomaly_level,
            "data_size_mb": data_size_mb,
            "qos_vector": qos_vector,
            "actual_latency_ms": actual_latency_ms,
            "actual_loss_pct": actual_loss_pct,
            "detection_accuracy": detection_accuracy,
        }
        self.history_data.append(sample)

    def fit(self) -> bool:
        """训练模型"""
        if len(self.history_data) < 100:
            return False
        # TODO: 实现MLP训练
        return True


# 全局实例
_translator_instance = None


def get_qos_translator() -> QoSTranslatorV3:
    """获取全局QoS翻译器实例"""
    global _translator_instance
    if _translator_instance is None:
        _translator_instance = QoSTranslatorV3()
    return _translator_instance