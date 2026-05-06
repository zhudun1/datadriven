import torch
import os
import numpy as np
import torch.nn.functional as F
import sys

# 添加项目根目录到 sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)  # insert instead of append

# 核心模型导入
from models.features import MultimodalFeatures
from models.feature_transfer_nets import FeatureProjectionMLP

class CFMDetector:

    def __init__(self, class_name, checkpoint_path, device="cuda"):
        self.device = device if torch.cuda.is_available() else "cpu"
        self.class_name = class_name
        self.checkpoint_path = checkpoint_path
        self.feature_extractor = MultimodalFeatures()

        # 初始化特征提取器和映射网络
        self.cfm_2to3 = FeatureProjectionMLP(in_features=768, out_features=1152).to(self.device)
        self.cfm_3to2 = FeatureProjectionMLP(in_features=1152, out_features=768).to(self.device)

        # 加载权重
        path_2to3 = os.path.join(self.checkpoint_path, class_name, f"CFM_2Dto3D_{class_name}_50ep_4bs.pth")
        path_3to2 = os.path.join(self.checkpoint_path, class_name, f"CFM_3Dto2D_{class_name}_50ep_4bs.pth")

        if not os.path.exists(path_2to3):
            raise FileNotFoundError(f"找不到权重文件: {path_2to3}")

        self.cfm_2to3.load_state_dict(torch.load(path_2to3, map_location=self.device))
        self.cfm_3to2.load_state_dict(torch.load(path_3to2, map_location=self.device))
        self.cfm_2to3.eval(); self.cfm_3to2.eval()

        # 初始化滤波器
        self.w_l, self.w_u = 5, 7
        self.pad_l, self.pad_u = 2, 3
        self.weight_l = torch.ones(1, 1, self.w_l, self.w_l, device=self.device) / (self.w_l**2)
        self.weight_u = torch.ones(1, 1, self.w_u, self.w_u, device=self.device) / (self.w_u**2)

        print(f"成功加载模型并初始化滤波器: {class_name}")

    # ===================== get_anomaly_score =====================
    def get_anomaly_score(self, rgb, pc):
        rgb, pc = rgb.to(self.device), pc.to(self.device)

        with torch.no_grad():
            # 特征提取
            rgb_patch, xyz_patch = self.feature_extractor.get_features_maps(rgb, pc)
            rgb_feat_pred = self.cfm_3to2(xyz_patch)
            xyz_feat_pred = self.cfm_2to3(rgb_patch)

            # 掩码处理
            xyz_mask = (xyz_patch.sum(axis=-1) == 0)

            # 余弦距离计算
            cos_3d = (F.normalize(xyz_feat_pred, dim=1) - F.normalize(xyz_patch, dim=1)).pow(2).sum(1).sqrt()
            cos_3d[xyz_mask] = 0.

            cos_2d = (F.normalize(rgb_feat_pred, dim=1) - F.normalize(rgb_patch, dim=1)).pow(2).sum(1).sqrt()
            cos_2d[xyz_mask] = 0.

            # 组合分数 + 滤波
            cos_comb = (cos_2d * cos_3d).reshape(1, 1, 224, 224)

            for _ in range(5):
                cos_comb = F.conv2d(input=cos_comb, padding=self.pad_l, weight=self.weight_l)
            for _ in range(3):
                cos_comb = F.conv2d(input=cos_comb, padding=self.pad_u, weight=self.weight_u)

            cos_comb = cos_comb.squeeze() # (224, 224)

            # 返回最大异常分数
            score = cos_comb.max().item()
            return score