import numpy as np

class QoSTranslator:
    def __init__(self):
        self.rules = {
            
            "CRITICAL": {"bandwidth": 80.0, "latency": 25.0, "priority": 6, "loss_rate": 0.01},
            "HIGH":     {"bandwidth": 60.0,  "latency": 50.0, "priority": 4, "loss_rate": 0.05},
            "MEDIUM":   {"bandwidth": 30.0,  "latency": 100.0, "priority": 2, "loss_rate": 0.1},
            "NORMAL":   {"bandwidth": 10.0,  "latency": 200.0, "priority": 0, "loss_rate": 0.2}
        }
        
        self.max_bandwidth = 100.0  
        self.max_latency = 500.0    
        self.max_priority = 6.0
        self.max_loss_rate = 0.2
        
        self.thresholds = {"CRITICAL": 0.28, "HIGH": 0.22, "MEDIUM": 0.16}

    def translate(self, cfm_score):
        # 确定风险等级
        if cfm_score >= self.thresholds["CRITICAL"]:
            level = "CRITICAL"
        elif cfm_score >= self.thresholds["HIGH"]:
            level = "HIGH"
        elif cfm_score >= self.thresholds["MEDIUM"]:
            level = "MEDIUM"
        else:
            level = "NORMAL"
        
        # 获取原始QoS参数
        qos_dict = self.rules[level]
        
        # 归一化到0-1区间并转为numpy数组 [带宽, 时延, 优先级, 丢包率]
        qos_vector = np.array([
            qos_dict["bandwidth"] / self.max_bandwidth,
            qos_dict["latency"] / self.max_latency,
            qos_dict["priority"] / self.max_priority,
            qos_dict["loss_rate"] / self.max_loss_rate
        ], dtype=np.float32)
        
        return level, qos_vector