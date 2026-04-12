from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv 
from common.utils import init_logger
import numpy as np 

logger = init_logger()

class GraphPPOOrchestrator:
    def __init__(self, env):

        self.env = env
        self.vec_env = DummyVecEnv([lambda: self.env])
        self.model = PPO(
            "MlpPolicy",
            self.vec_env,
            verbose=1,
            learning_rate=3e-4,
            gamma=0.99,
            ent_coef=0.05,
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
        self.model.save("./ppo_graph_model")
        logger.info("PPO模型训练完成")

    def predict(self, obs: np.ndarray) -> dict:
        if obs.ndim == 1:
            obs = obs[None, :]

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