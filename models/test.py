# 新建test_data_loader.py，放在models/目录下
from dataset import get_data_loader
import torch

# 加载测试集的点云数据
dataset_path = "/var/data-driven/crossmodal-feature-mapping/datasets/mvtec3d"
test_loader = get_data_loader("test", class_name="cable_gland", img_size=224, dataset_path=dataset_path)
# 迭代一个batch，校验点云格式
for (rgb, pc, depth), gt, label, rgb_path in test_loader:
    print("点云数据格式：", pc.dtype)
    print("点云数据维度：", pc.shape)  # 预期：[B, C, 224, 224]（B为batch size，默认一般是1）
    print("点云设备：", pc.device)
    break