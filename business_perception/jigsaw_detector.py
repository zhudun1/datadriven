"""
Jigsaw-VAD 异常检测器
用于视频和图像的异常检测
"""

import os
import sys
import numpy as np
import torch
import torch.nn.functional as F
from typing import Union, Dict, List
import cv2

class JigsawVADDetector:
    def __init__(self, checkpoint_path: str, time_length: int = 7, device: str = "cuda"):
        self.device = device if torch.cuda.is_available() else "cpu"
        self.time_length = time_length
        self.half_t = time_length // 2

        # 获取项目根目录
        BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        JIGSAW_PATH = os.path.join(BASE_DIR, "Jigsaw-VAD-main")
        JIGSAW_MODELS = os.path.join(JIGSAW_PATH, "models")

        # 清理并重新添加路径
        for p in [JIGSAW_MODELS, JIGSAW_PATH]:
            if p in sys.path:
                sys.path.remove(p)

        # 关键：先添加JIGSAW_PATH，再添加models子目录
        # 这样Python会先在JIGSAW_PATH下查找models.model
        sys.path.insert(0, JIGSAW_PATH)
        sys.path.insert(0, JIGSAW_MODELS)

        # 强制重新加载sys.modules中的models，避免缓存冲突
        if 'models' in sys.modules:
            del sys.modules['models']
        if 'models.model' in sys.modules:
            del sys.modules['models.model']

        import importlib.util
        model_file = os.path.join(JIGSAW_MODELS, "model.py")
        spec = importlib.util.spec_from_file_location("jigsaw_model", model_file)
        model_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(model_module)
        WideBranchNet = model_module.WideBranchNet

        self.model = WideBranchNet(
            time_length=time_length,
            num_classes=[time_length ** 2, 81]
        ).to(self.device)

        if os.path.exists(checkpoint_path):
            state_dict = torch.load(checkpoint_path, map_location=self.device)
            self.model.load_state_dict(state_dict, strict=True)
            print(f"Loaded Jigsaw-VAD checkpoint from {checkpoint_path}")

        self.model.eval()

    def preprocess_frame(self, frame, target_size=64):
        frame = cv2.resize(frame, (target_size, target_size))
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame = frame.astype(np.float32) / 255.0
        frame = torch.from_numpy(frame).permute(2, 0, 1)
        return frame

    def extract_frames_sequence(self, video_path, center_frame, bbox=None):
        frames = []
        cap = cv2.VideoCapture(video_path)

        for f in range(center_frame - self.half_t, center_frame + self.half_t + 1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ret, frame = cap.read()
            if not ret:
                frame = np.zeros((240, 360, 3), dtype=np.uint8)

            if bbox is not None:
                x1, y1, x2, y2 = map(int, bbox)
                h, w = frame.shape[:2]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                frame = frame[y1:y2, x1:x2]

            frame = self.preprocess_frame(frame)
            frames.append(frame)

        cap.release()
        frames = torch.stack(frames, dim=1)  # (C, T, H, W)
        return frames

    def compute_jigsaw_score(self, frame_sequence):
        obj = frame_sequence.unsqueeze(0).to(self.device)

        with torch.no_grad():
            temp_logits, spat_logits = self.model(obj)

            temp_logits = temp_logits.view(-1, self.time_length, self.time_length)
            spat_logits = spat_logits.view(-1, 9, 9)

            spat_probs = F.softmax(spat_logits, -1)
            diag = torch.diagonal(spat_probs, offset=0, dim1=-2, dim2=-1)
            spatial_conf = diag.min(-1)[0].cpu().item()

            temp_probs = F.softmax(temp_logits, -1)
            diag2 = torch.diagonal(temp_probs, offset=0, dim1=-2, dim2=-1)
            temporal_conf = diag2.min(-1)[0].cpu().item()

        return {
            'spatial_confidence': spatial_conf,
            'temporal_confidence': temporal_conf,
            'spatial_score': 1 - spatial_conf,
            'temporal_score': 1 - temporal_conf,
            'combined_score': 1 - (spatial_conf + temporal_conf) / 2
        }

    def detect_video(self, video_path):
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        start_frame = self.half_t
        end_frame = total_frames - self.half_t

        frame_scores = []
        for frame_idx in range(start_frame, min(end_frame, start_frame + 100)):
            seq = self.extract_frames_sequence(video_path, frame_idx)
            scores = self.compute_jigsaw_score(seq)
            frame_scores.append(scores['combined_score'])

        if not frame_scores:
            return 0.5

        return float(np.mean(frame_scores))

    def detect_image(self, image_path):
        frame = cv2.imread(image_path)
        if frame is None:
            return 0.5

        frame = self.preprocess_frame(frame)
        frames = torch.stack([frame] * self.time_length, dim=1)
        scores = self.compute_jigsaw_score(frames)

        return scores['combined_score']

    def get_anomaly_score(self, video_path=None, image_path=None):
        """统一检测接口，兼容已有代码

        Args:
            video_path: 视频文件路径
            image_path: 图像文件路径

        Returns:
            float: 异常分数 (0-1, 越高越异常)
        """
        if video_path:
            return self.detect_video(video_path)
        elif image_path:
            return self.detect_image(image_path)
        else:
            return 0.5