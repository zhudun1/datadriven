"""
Mock pointnet2_ops for Windows without CUDA
Provides dummy implementations to allow code to run
"""
import torch
import numpy as np


def furthest_point_sample(xyz, npoint):
    """Simulate FPS sampling - returns (B, npoint) indices like real PointNet2"""
    B, N, C = xyz.shape
    centroids = torch.zeros(B, npoint, dtype=torch.long, device=xyz.device)
    for i in range(B):
        centroids[i] = torch.randperm(N, device=xyz.device)[:npoint]
    return centroids


def gather_operation(features, idx):
    """Gather points - features: (B, C, N), idx: (B, npoint)"""
    # Real PointNet2: output is (B, C, npoint)
    B, C, N = features.shape
    idx_B = idx[:, :, None].expand(-1, -1, C)  # (B, npoint, C)
    idx_N = idx[:, None, :].expand(B, C, -1)   # (B, C, npoint)
    # Actually just gather: result[b, c, j] = features[b, c, idx[b, j]]
    # Simpler: reshape and use advanced indexing
    result = torch.zeros(B, C, idx.shape[1], device=features.device, dtype=features.dtype)
    for b in range(B):
        for j in range(idx.shape[1]):
            result[b, :, j] = features[b, :, idx[b, j].long()]
    return result


def square_distance(src, dst):
    """Calculate squared distance"""
    B, N, _ = src.shape
    _, M, _ = dst.shape
    dist = -2 * torch.matmul(src, dst.permute(0, 2, 1))
    dist += torch.sum(src**2, -1).view(B, N, 1)
    dist += torch.sum(dst**2, -1).view(B, 1, M)
    return dist


def knn_point(k, xyz, query_xyz):
    """Find k nearest neighbors"""
    dist = square_distance(query_xyz, xyz)
    _, i = torch.topk(dist, k, dim=-1, largest=False)
    return i


# Module with functions
class pointnet2_utils:
    furthest_point_sample = staticmethod(furthest_point_sample)
    gather_operation = staticmethod(gather_operation)
    square_distance = staticmethod(square_distance)
    knn_point = staticmethod(knn_point)


__all__ = ["furthest_point_sample", "gather_operation", "square_distance", "knn_point", "pointnet2_utils"]