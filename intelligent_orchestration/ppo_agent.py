from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
from common.utils import init_logger
import numpy as np
import os
import random

logger = init_logger()

# 模型保存路径（在项目根目录）
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ppo_graph_model.zip")

class GraphPPOOrchestrator:
    def __init__(self, env, epsilon: float = 0.3):
        """
        epsilon: 随机探索概率，默认30%的概率随机选择节点
        """
        self.env = env
        self.vec_env = DummyVecEnv([lambda: self.env])
        self.epsilon = epsilon  # 随机探索概率

        # 检查模型文件是否存在
        if os.path.exists(MODEL_PATH):
            logger.info(f"加载已有PPO模型: {MODEL_PATH}")
            self.model = PPO.load(MODEL_PATH, self.vec_env)
        else:
            logger.info("创建新PPO模型")
            self.model = PPO(
                "MlpPolicy",
                self.vec_env,
                verbose=1,
                learning_rate=3e-4,
                gamma=0.99,
                ent_coef=0.2,  # 增加探索系数，鼓励尝试不同节点
                batch_size=32,
                n_steps=512,
                n_epochs=10,
                clip_range=0.2,
                max_grad_norm=1.0,
            )

    def train_warmup(self, timesteps: int = 20000):
        logger.info(f"开始PPO预热训练，步数：{timesteps}")
        self.model.learn(
            total_timesteps=timesteps,
            reset_num_timesteps=True
        )
        self.model.save(MODEL_PATH)
        logger.info(f"PPO模型训练完成，保存到: {MODEL_PATH}")

    def predict(self, obs: np.ndarray) -> dict:
        if obs.ndim == 1:
            obs = obs[None, :]

        # epsilon-greedy: 一定概率随机选择节点，避免总是选择固定节点
        if random.random() < self.epsilon:
            logger.info(f"随机探索模式 (epsilon={self.epsilon})")
            action_flat = self.env.action_space.sample()
        else:
            try:
                # 尝试加载模型
                action_flat, _ = self.model.predict(obs, deterministic=True)
                action_flat = action_flat[0]
            except Exception as e:
                logger.warning(f"预测出错，使用随机动作: {e}")
                action_flat = self.env.action_space.sample()

        # 还原为业务所需的字典格式（只有vnf_node）
        return self._unflatten_action(action_flat)

    def _unflatten_action(self, action_flat: np.ndarray) -> dict:
        # 新的动作格式：只有VNF节点
        vnf_node = action_flat[:self.env.vnf_count].tolist()
        return {"vnf_node": vnf_node}