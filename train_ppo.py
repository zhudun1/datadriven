#!/usr/bin/env python3
"""PPO模型重新训练脚本 - 使用QoS翻译器生成真实分布训练数据"""
import sys
import os
import random
import numpy as np

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from intelligent_orchestration.vne_env import GraphVNEEnv


def sample_real_scenario():
    """真实分布采样 - critical/high占80%

    Returns:
        dict: 包含anomaly_score, data_size_mb, data_type, business_scenario
    """
    # 80%: high priority (critical/high), 20%: normal/monitoring
    if random.random() < 0.8:
        # 高优先级场景
        anomaly_score = random.uniform(0.5, 1.0)
    else:
        # 正常/监控场景
        anomaly_score = random.uniform(0.0, 0.5)

    # 数据量与异常分数相关 - 高异常分数通常数据量大
    if anomaly_score >= 0.75:
        data_size_mb = random.uniform(20.0, 100.0)  # 大数据
    elif anomaly_score >= 0.5:
        data_size_mb = random.uniform(5.0, 50.0)   # 中等数据
    else:
        data_size_mb = random.uniform(0.1, 10.0)    # 小数据

    # 数据类型
    data_types = ["image", "video", "pointcloud"]
    if data_size_mb > 50:
        data_type = "pointcloud"  # 大数据通常是点云
    elif data_size_mb > 10:
        data_type = "video"
    else:
        data_type = "image"

    # 业务场景 - 根据异常分数选择
    if anomaly_score >= 0.75:
        # critical级别
        scenarios = ["safety", "quality"]
        business_scenario = random.choice(scenarios)
    elif anomaly_score >= 0.5:
        # high级别
        scenarios = ["safety", "quality", "maintenance"]
        business_scenario = random.choice(scenarios)
    else:
        # normal/medium级别
        scenarios = ["monitoring", "maintenance", "quality"]
        business_scenario = random.choice(scenarios)

    return {
        "anomaly_score": anomaly_score,
        "data_size_mb": data_size_mb,
        "data_type": data_type,
        "business_scenario": business_scenario
    }


# 先创建环境获取正确的观察空间维度
env = GraphVNEEnv(use_db=False)
obs_dim = env.observation_space.shape[0]
action_nvec = list(env.action_space.nvec)

print(f"环境信息:")
print(f"  - 观察空间维度: {obs_dim}")
print(f"  - 动作空间: {action_nvec}")
print(f"  - 节点数量: {env.node_count}")
print(f"  - 链路数量: {env.link_count}")

# 训练参数
TIMESTEPS = 20000  # 与原训练步数一致

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
from common.utils import init_logger

logger = init_logger()

def make_env():
    """创建训练环境 - 使用真实分布采样"""
    def _init():
        # 使用真实分布采样生成QoS参数
        scenario = sample_real_scenario()
        env = GraphVNEEnv(use_db=False)
        # 使用options传递QoS参数，触发翻译器
        env.reset(options=scenario)
        return env
    return _init

def train_ppo(timesteps: int = TIMESTEPS):
    """训练PPO模型"""
    logger.info(f"开始新的PPO训练，步数：{timesteps}")
    logger.info(f"注意：新状态空间维度为{obs_dim}（vnr_feat_dim=7，使用6维QoS向量）")

    # 创建多个并行环境以提高训练稳定性
    vec_env = DummyVecEnv([make_env() for _ in range(1)])

    # 检查动作空间与PPO兼容
    # 获取动作空间的形状
    action_space = vec_env.action_space
    print(f"\n训练配置:")
    print(f"  - 向量环境数量: 1")
    print(f"  - 动作空间: {action_space}")
    print(f"  - 训练步数: {timesteps}")

    # 创建PPO模型
    model = PPO(
        "MlpPolicy",
        vec_env,
        verbose=1,
        learning_rate=3e-4,
        gamma=0.99,
        ent_coef=0.01,  # 降低探索系数
        batch_size=64,
        n_steps=512,
        n_epochs=10,
        clip_range=0.2,
        max_grad_norm=1.0,
        tensorboard_log="./ppo_logs"
    )

    print(f"\n开始训练...")
    # 训练 (不使用进度条)
    model.learn(
        total_timesteps=timesteps,
        progress_bar=False
    )

    # 保存模型
    save_path = "./ppo_graph_model"
    model.save(save_path)
    logger.info(f"PPO模型训练完成并保存到: {save_path}")

    # 验证模型
    print(f"\n验证模型...")
    obs = vec_env.reset()
    action, _ = model.predict(obs, deterministic=True)
    print(f"  - 测试动作: {action}")
    print(f"  - 观察形状: {obs.shape}")

    return model

if __name__ == "__main__":
    try:
        model = train_ppo(TIMESTEPS)
        print("\n训练成功完成!")
    except Exception as e:
        print(f"\n训练失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)