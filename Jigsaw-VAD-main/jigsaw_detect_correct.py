"""
Jigsaw-VAD Anomaly Detection - Correct Implementation
Based on eval_complete.py approach
"""

import os
import sys
import pickle
import argparse
import numpy as np
import cv2
import torch
import torch.nn.functional as F
from torch import nn
from tqdm import tqdm
from scipy.ndimage import gaussian_filter1d

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from models.model import WideBranchNet


class JigsawDetector:
    """
    Jigsaw-VAD Anomaly Detector
    Correct implementation: smooth -> normalize -> invert to anomaly
    """

    def __init__(self, checkpoint_path, time_length=7, device='cuda'):
        self.time_length = time_length
        self.half_t = time_length // 2
        self.device = device

        # sample_num=7 -> temporal=49, spatial=81
        num_classes = [time_length ** 2, 81]
        self.net = WideBranchNet(time_length=time_length, num_classes=num_classes)
        self.net.to(device)
        self.net.eval()

        if os.path.exists(checkpoint_path):
            state = torch.load(checkpoint_path, map_location=device)
            self.net.load_state_dict(state, strict=True)
            print(f"Loaded checkpoint from {checkpoint_path}")

    def preprocess_frame(self, frame, target_size=64):
        """Preprocess a single frame"""
        frame = cv2.resize(frame, (target_size, target_size))
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame = frame.astype(np.float32) / 255.0
        return frame

    def compute_jigsaw_scores(self, frame_sequence):
        """Compute jigsaw puzzle scores (confidence)"""
        obj = frame_sequence.unsqueeze(0).to(self.device)

        with torch.no_grad():
            temp_logits, spat_logits = self.net(obj)

            # Reshape to permutation matrices
            temp_logits = temp_logits.view(-1, self.time_length, self.time_length)
            spat_logits = spat_logits.view(-1, 9, 9)

            # Spatial jigsaw: 3x3 patches
            spat_probs = F.softmax(spat_logits, -1)
            diag = torch.diagonal(spat_probs, offset=0, dim1=-2, dim2=-1)
            spatial_conf = diag.min(-1)[0].cpu().item()

            # Temporal jigsaw: T frames
            temp_probs = F.softmax(temp_logits, -1)
            diag2 = torch.diagonal(temp_probs, offset=0, dim1=-2, dim2=-1)
            temporal_conf = diag2.min(-1)[0].cpu().item()

        return spatial_conf, temporal_conf

    def detect_video_from_crops(self, crops_dir, video_key):
        """
        Detect anomalies using pre-extracted object crops
        Correct approach: smooth first, normalize, then invert to anomaly
        """
        crop_files = sorted([f for f in os.listdir(crops_dir) if f.startswith(video_key) and f.endswith('.jpg')])

        if not crop_files:
            return {'error': f'No crops found for {video_key}'}

        # Group crops by frame
        frame_crops = {}
        for crop_file in crop_files:
            parts = crop_file.replace('.jpg', '').split('_')
            frame_idx = int(parts[1][1:])

            crop_path = os.path.join(crops_dir, crop_file)
            crop = cv2.imread(crop_path)
            if crop is not None:
                if frame_idx not in frame_crops:
                    frame_crops[frame_idx] = []
                frame_crops[frame_idx].append(crop)

        # Compute scores for each frame
        frame_scores = []  # Combined confidence
        frame_spatial = []  # Spatial confidence
        frame_temporal = []  # Temporal confidence

        for frame_idx in sorted(frame_crops.keys()):
            crops = frame_crops[frame_idx]

            # Get min score across objects (most anomalous detection)
            obj_spatial = []
            obj_temporal = []

            for crop in crops:
                frames = []
                for _ in range(self.time_length):
                    frames.append(self.preprocess_frame(crop))

                frames = np.stack(frames, axis=0)
                frames = frames.transpose(3, 0, 1, 2)
                seq = torch.from_numpy(frames).float()

                s_conf, t_conf = self.compute_jigsaw_scores(seq)
                obj_spatial.append(s_conf)
                obj_temporal.append(t_conf)

            if obj_spatial:
                min_s = min(obj_spatial)
                min_t = min(obj_temporal)
                frame_spatial.append(min_s)
                frame_temporal.append(min_t)
                frame_scores.append(0.5 * min_s + 0.5 * min_t)

        if not frame_scores:
            return {'error': 'No valid scores'}

        # Convert to arrays
        frame_spatial = np.array(frame_spatial)
        frame_temporal = np.array(frame_temporal)
        frame_scores = np.array(frame_scores)

        # Step 1: Apply Gaussian smoothing FIRST
        spatial_smooth = gaussian_filter1d(frame_spatial, sigma=10)
        temporal_smooth = gaussian_filter1d(frame_temporal, sigma=10)
        combined_smooth = gaussian_filter1d(frame_scores, sigma=10)

        # Step 2: Normalize (0-1 range)
        def normalize(x):
            if x.max() - x.min() < 1e-8:
                return x
            return (x - x.min()) / (x.max() - x.min())

        spatial_norm = normalize(spatial_smooth)
        temporal_norm = normalize(temporal_smooth)
        combined_norm = normalize(combined_smooth)

        # Step 3: Invert to get ANOMALY scores
        # High confidence = low anomaly, Low confidence = high anomaly
        spatial_anomaly = 1 - spatial_norm
        temporal_anomaly = 1 - temporal_norm
        combined_anomaly = 1 - combined_norm

        return {
            'video': video_key,
            'frame_count': len(frame_scores),
            'frame_confidence': frame_scores.tolist(),
            'frame_anomaly_scores': combined_anomaly.tolist(),
            'spatial_anomaly': spatial_anomaly.tolist(),
            'temporal_anomaly': temporal_anomaly.tolist(),
            # Video-level scores: average of smoothed anomaly scores
            'video_score': float(combined_anomaly.mean()),
            'video_spatial': float(spatial_anomaly.mean()),
            'video_temporal': float(temporal_anomaly.mean()),
            # Also provide confidence scores for reference
            'video_confidence': float(combined_smooth.mean()),
        }


def main():
    parser = argparse.ArgumentParser(description="Jigsaw-VAD Detection")
    parser.add_argument("--mode", type=str, default="crops", choices=['frames', 'crops'])
    parser.add_argument("--data_dir", type=str,
                        default="/var/data-driven/Jigsaw-VAD-main/Avenue Dataset")
    parser.add_argument("--split", type=str, default="test", choices=['train', 'test'])
    parser.add_argument("--checkpoint", type=str,
                        default="/var/data-driven/Jigsaw-VAD-main/checkpoints/avenue_92.18.pth")
    parser.add_argument("--time_length", type=int, default=7)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--device", type=str, default="cuda")
    args = parser.parse_args()

    print("=" * 60)
    print("Jigsaw-VAD Anomaly Detection (Correct Implementation)")
    print("=" * 60)

    detector = JigsawDetector(args.checkpoint, time_length=args.time_length, device=args.device)

    if args.mode == 'crops':
        data_dir = os.path.join(args.data_dir, f"extracted_crops_{args.split}")
    else:
        data_dir = os.path.join(args.data_dir, f"extracted_frames_{args.split}")

    video_dirs = sorted([d for d in os.listdir(data_dir) if os.path.isdir(os.path.join(data_dir, d))])

    print(f"\nProcessing {len(video_dirs)} videos...")

    all_results = []
    for video_key in tqdm(video_dirs):
        video_dir = os.path.join(data_dir, video_key)
        result = detector.detect_video_from_crops(video_dir, video_key)
        all_results.append(result)

        if 'error' not in result:
            print(f"\n{video_key}: anomaly={result['video_score']:.4f}, confidence={result['video_confidence']:.4f}")

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    valid = [r for r in all_results if 'error' not in r]
    scores = [r['video_score'] for r in valid]

    print(f"\nProcessed: {len(valid)}/{len(all_results)} videos")
    print(f"Mean Anomaly Score: {np.mean(scores):.4f}")
    print(f"Std: {np.std(scores):.4f}")
    print(f"Range: [{np.min(scores):.4f}, {np.max(scores):.4f}]")

    if args.output:
        with open(args.output, 'wb') as f:
            pickle.dump(all_results, f)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()