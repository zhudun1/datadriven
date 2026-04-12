import torch


def interpolating_points(xyz: torch.Tensor, centers: torch.Tensor, center_features: torch.Tensor) -> torch.Tensor:
    """
    Interpolate sparse center features to dense point set by nearest-center assignment.
    xyz: [B, 3, N]
    centers: [B, 3, G]
    center_features: [B, C, G]
    return: [B, C, N]
    """
    if xyz.ndim != 3 or centers.ndim != 3 or center_features.ndim != 3:
        raise ValueError("Expected xyz/centers/features to be 3D tensors")

    xyz_pts = xyz.transpose(1, 2).contiguous()  # [B, N, 3]
    center_pts = centers.transpose(1, 2).contiguous()  # [B, G, 3]

    dist = torch.cdist(xyz_pts, center_pts, p=2)  # [B, N, G]
    nearest_center_idx = dist.argmin(dim=2)  # [B, N]

    feat = center_features.transpose(1, 2).contiguous()  # [B, G, C]
    gather_idx = nearest_center_idx.unsqueeze(-1).expand(-1, -1, feat.shape[-1])  # [B, N, C]
    interpolated = torch.gather(feat, dim=1, index=gather_idx)  # [B, N, C]
    return interpolated.transpose(1, 2).contiguous()  # [B, C, N]

