"""
Jigsaw-VAD Video Anomaly Detection - Correct Temporal Processing
Processes video frames directly to ensure proper temporal sequence
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


class JigsawVideoDetector:
    """
    Jigsaw-VAD Video Anomaly Detector
    Correctly processes temporal sequences from video
    """

    def __init__(self, checkpoint_path, time_length=7, device='cuda'):
        self.time_length = time_length
        self.half_t = time_length // 2
        self.device = device

        num_classes = [time_length ** 2, 81]
        self.net = WideBranchNet(time_length=time_length, num_classes=num_classes)
        self.net.to(device)
        self.net.eval()

        if os.path.exists(checkpoint_path):
            state = torch.load(checkpoint_path, map_location=device)
            self.net.load_state_dict(state, strict=True)
            print(f"Loaded checkpoint from {checkpoint_path}")

    def preprocess_frame(self, frame, target_size=64):
        """Preprocess a frame"""
        frame = cv2.resize(frame, (target_size, target_size))
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame = frame.astype(np.float32) / 255.0
        return frame

    def compute_jigsaw_scores(self, frame_sequence):
        """Compute jigsaw scores (confidence) for a frame sequence"""
        obj = frame_sequence.unsqueeze(0).to(self.device)

        with torch.no_grad():
            temp_logits, spat_logits = self.net(obj)

            temp_logits = temp_logits.view(-1, self.time_length, self.time_length)
            spat_logits = spat_logits.view(-1, 9, 9)

            spat_probs = F.softmax(spat_logits, -1)
            diag = torch.diagonal(spat_probs, offset=0, dim1=-2, dim2=-1)
            spatial_conf = diag.min(-1)[0].cpu().item()

            temp_probs = F.softmax(temp_logits, -1)
            diag2 = torch.diagonal(temp_probs, offset=0, dim1=-2, dim2=-1)
            temporal_conf = diag2.min(-1)[0].cpu().item()

        return spatial_conf, temporal_conf

    def extract_sequence_from_video(self, video_path, center_frame, bbox=None):
        """Extract frame sequence from video, applying bbox crop to each frame"""
        frames = []
        cap = cv2.VideoCapture(video_path)

        for f in range(center_frame - self.half_t, center_frame + self.half_t + 1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ret, frame = cap.read()
            if not ret:
                cap.release()
                return None

            # Apply bbox crop if provided
            if bbox is not None:
                x1, y1, x2, y2 = map(int, bbox)
                h, w = frame.shape[:2]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                frame = frame[y1:y2, x1:x2]

            frame = self.preprocess_frame(frame)
            frames.append(frame)

        cap.release()

        frames = np.stack(frames, axis=0)
        frames = frames.transpose(3, 0, 1, 2)  # (T, H, W, C) -> (C, T, H, W)
        return torch.from_numpy(frames).float()

    def detect_video(self, video_path, detections=None, video_key=None, filter_ratio=0.8):
        """
        Detect anomalies in a video

        Args:
            video_path: Path to video file
            detections: YOLO detection dict (if None, uses full frames)
            video_key: Video key for looking up detections
            filter_ratio: YOLO confidence threshold

        Returns:
            Detection result dict
        """
        if video_key is None:
            video_key = os.path.basename(video_path).replace('.avi', '').replace('.mp4', '')

        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        # Determine valid frame range
        start_frame = self.half_t
        end_frame = total_frames - self.half_t

        frame_scores = []
        frame_spatial = []
        frame_temporal = []

        if detections is not None and video_key in detections:
            # Use YOLO detections
            dets_dict = detections[video_key]

            for frame_idx in range(start_frame, end_frame):
                if frame_idx >= len(dets_dict):
                    break

                dets = dets_dict[frame_idx]
                if len(dets) == 0:
                    continue

                dets = dets[dets[:, 4] > filter_ratio, :]
                if len(dets) == 0:
                    continue

                # Compute scores for each detection, take min
                obj_spatial = []
                obj_temporal = []

                for loc in dets:
                    bbox = loc[:4]
                    seq = self.extract_sequence_from_video(video_path, frame_idx, bbox)
                    if seq is None:
                        continue

                    s_conf, t_conf = self.compute_jigsaw_scores(seq)
                    obj_spatial.append(s_conf)
                    obj_temporal.append(t_conf)

                if obj_spatial:
                    frame_spatial.append(min(obj_spatial))
                    frame_temporal.append(min(obj_temporal))
                    frame_scores.append(0.5 * min(obj_spatial) + 0.5 * min(obj_temporal))
        else:
            # Use full frames
            for frame_idx in range(start_frame, end_frame):
                seq = self.extract_sequence_from_video(video_path, frame_idx)
                if seq is None:
                    continue

                s_conf, t_conf = self.compute_jigsaw_scores(seq)
                frame_spatial.append(s_conf)
                frame_temporal.append(t_conf)
                frame_scores.append(0.5 * s_conf + 0.5 * t_conf)

        if not frame_scores:
            return {'video': video_key, 'error': 'No valid detections', 'frame_count': 0}

        # Convert to arrays
        frame_spatial = np.array(frame_spatial)
        frame_temporal = np.array(frame_temporal)
        frame_scores = np.array(frame_scores)

        # Step 1: Smooth
        spatial_smooth = gaussian_filter1d(frame_spatial, sigma=10)
        temporal_smooth = gaussian_filter1d(frame_temporal, sigma=10)
        combined_smooth = gaussian_filter1d(frame_scores, sigma=10)

        # Step 2: Normalize
        def normalize(x):
            if x.max() - x.min() < 1e-8:
                return x
            return (x - x.min()) / (x.max() - x.min())

        spatial_norm = normalize(spatial_smooth)
        temporal_norm = normalize(temporal_smooth)
        combined_norm = normalize(combined_smooth)

        # Step 3: Invert to anomaly
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
            'video_score': float(combined_anomaly.mean()),
            'video_spatial': float(spatial_anomaly.mean()),
            'video_temporal': float(temporal_anomaly.mean()),
            'video_confidence': float(combined_smooth.mean()),
        }


def main():
    parser = argparse.ArgumentParser(description="Jigsaw-VAD Video Detection")
    parser.add_argument("--video_dir", type=str, required=True,
                        help="Directory containing videos")
    parser.add_argument("--detections", type=str, required=True,
                        help="Path to YOLO detection pkl")
    parser.add_argument("--checkpoint", type=str,
                        default="/var/data-driven/Jigsaw-VAD-main/checkpoints/avenue_92.18.pth")
    parser.add_argument("--time_length", type=int, default=7)
    parser.add_argument("--filter_ratio", type=float, default=0.8)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--device", type=str, default="cuda")
    args = parser.parse_args()

    print("=" * 60)
    print("Jigsaw-VAD Video Anomaly Detection")
    print("=" * 60)

    # Load detections
    with open(args.detections, 'rb') as f:
        detections = pickle.load(f)
    print(f"Loaded detections for {len(detections)} videos")

    detector = JigsawVideoDetector(args.checkpoint, time_length=args.time_length, device=args.device)

    video_files = sorted([f for f in os.listdir(args.video_dir) if f.endswith('.avi')])
    print(f"\nProcessing {len(video_files)} videos...")

    all_results = []
    for video_file in tqdm(video_files):
        video_path = os.path.join(args.video_dir, video_file)
        result = detector.detect_video(video_path, detections)
        all_results.append(result)

        if 'error' not in result:
            print(f"\n{video_file}: anomaly={result['video_score']:.4f}")

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