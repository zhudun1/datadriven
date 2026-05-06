# 编排算法修改设计方案

## 一、修改目标

在**不改变**现有功能输出的情况下，将PPO训练从随机QoS输入改为使用QoS翻译器生成。

## 二、系统架构

```
训练流程:
┌─────────────────────────────────────────────────────┐
│  sample_real_scenario()                          │
│     ↓                                      │
│  异常分数+数据量+业务场景                      │
│     ↓                                      │
│  QoSTranslator.translate()                   │
│     ↓                                      │
│  6维QoS向量                              │
│     ↓                                      │
│  VNEEnv.set_qos_input()                     │
│     ↓                                      │
│  PPO.predict() → 节点部署                   │
│     ↓                                      │
│  reward = _calculate_reward()                │
│     ↓                                      │
│  PPO模型更新                              │
└─────────────────────────────────────────────────────┘
```

## 三、数据流变化

| 阶段 | 当前 | 修改后 |
|------|------|--------|
| 输入 | `reset()`随机生成4维QoS | `QoSTranslatorV3.translate()`生成6维QoS |
| 训练分布 | 4个随机场景 | 真实分布(critical/high占80%) |
| 输出 | 节点部署决策 | 保持不变 |

## 四、修改内容

### 1. VNE环境 (`vne_env.py`)

**新增方法:**
```python
def set_qos_input(self, anomaly_score: float, data_size_mb: float,
               data_type: str = "image", business_scenario: str = None):
    """设置外部QoS输入，触发翻译"""
    from business_perception.qos_translator import get_qos_translator
    translator = get_qos_translator()
    _, qos_vector, _ = translator.translate(
        anomaly_score, data_size_mb, data_type, business_scenario
    )
    self.set_qos_to_vnr(qos_vector)
```

**修改reset()支持两种模式:**
- 模式A: 外部输入 (`options`带参数) → 调用翻译器
- 模式B: 随机生成 (向后兼容)

### 2. 训练脚本 (`train_ppo.py`)

**新增真实分布采样:**
```python
def sample_real_scenario():
    """真实分布采样 - critical/high占80%"""
    if random.random() < 0.8:
        # 80%: high priority
        anomaly_score = random.uniform(0.5, 1.0)
    else:
        # 20%: normal/monitoring
        anomaly_score = random.uniform(0.0, 0.5)

    # 数据量与异常分数相关
    data_size_mb = anomaly_score * 100.0

    # 业务场景
    scenarios = ["safety", "quality", "maintenance", "monitoring"]
    business_scenario = random.choice(scenarios)

    return anomaly_score, data_size_mb, "image", business_scenario
```

### 3. 奖励计算

保持不变 - 当前实现已支持6维QoS检查。

## 五、关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 异常分数 | 0-1 | 随机生成，critical/high: 80% |
| 数据量 | 0.1-100MB | 与异常分数相关 |
| 业务场景 | safety/quality/maintenance/monitoring | 根据异常等级分布 |
| 训练步数 | 20000 | 与原一致 |
| 模型保存路径 | ppo_graph_model.zip | 同原路径 |

## 六、向后兼容

- `reset()` 无参数时保持原随机生成逻辑
- 现有API接口不变
- 输出格式不变

## 七、实施步骤

1. 修改 `vne_env.py` - 添加 `set_qos_input()` 方法
2. 修改 `train_ppo.py` - 添加真实分布采样函数
3. 运行训练
4. 验证结果