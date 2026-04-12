import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
import tifffile


def read_tiff_organized_pc(path: str) -> np.ndarray:
    try:
        arr = np.asarray(tifffile.imread(path), dtype=np.float32)
    except Exception:
        arr = np.asarray(Image.open(path), dtype=np.float32)
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    if arr.ndim == 3 and arr.shape[2] > 3:
        arr = arr[:, :, :3]
    return np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)


def organized_pc_to_depth_map(organized_pc: np.ndarray) -> np.ndarray:
    pc = np.asarray(organized_pc, dtype=np.float32)
    if pc.ndim == 3 and pc.shape[2] >= 3:
        depth = np.linalg.norm(pc[:, :, :3], axis=2)
    elif pc.ndim == 2:
        depth = pc
    else:
        raise ValueError(f"Unexpected point-cloud shape: {pc.shape}")
    return np.nan_to_num(depth, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


def resize_organized_pc(data, target_height: int = 224, target_width: int = 224) -> torch.Tensor:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    if arr.ndim != 3:
        raise ValueError(f"Expected HxWxC input, got shape={arr.shape}")

    tensor = torch.from_numpy(arr.transpose(2, 0, 1)).unsqueeze(0)
    tensor = torch.nan_to_num(tensor, nan=0.0, posinf=0.0, neginf=0.0)
    resized = F.interpolate(tensor, size=(target_height, target_width), mode="bilinear", align_corners=False)
    return resized.squeeze(0)
