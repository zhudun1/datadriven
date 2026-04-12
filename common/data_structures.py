import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Tuple

# 物理节点数据结构
@dataclass
class PhysicalNode:
    node_id: int
    cpu: float  # 剩余CPU（归一化0-1）
    memory: float  # 剩余内存（归一化0-1）
    energy_consumption: float  # 能耗系数（0.1-1.0，越高越耗能）

# 物理链路数据结构（节点间链路）
@dataclass
class PhysicalLink:
    link_id: int
    src_node: int  # 源节点ID
    dst_node: int  # 目的节点ID
    bandwidth: float  # 剩余带宽（归一化0-1）
    latency: float  # 时延（ms）
    path_id: int  # 候选路径ID（用于动作空间）

# 虚拟网络请求（VNR）：VNF链+QoS向量
@dataclass
class VNR:
    vnf_chain: List[int]  # VNF链 [vnf1_id, vnf2_id, ...]
    qos_vector: np.ndarray  # [带宽, 时延, 优先级, 丢包率]（归一化0-1）
    vnf_cpu_demand: List[float]  # 每个VNF的CPU需求
    vnf_mem_demand: List[float]  # 每个VNF的内存需求
    link_bw_demand: List[float]  # 每条虚拟链路的带宽需求

# 图结构拓扑（节点+链路）
@dataclass
class NetworkTopology:
    nodes: Dict[int, PhysicalNode]  # {node_id: PhysicalNode}
    links: Dict[int, PhysicalLink]  # {link_id: PhysicalLink}
    adj_matrix: np.ndarray  # 邻接矩阵（节点数×节点数，1表示有链路）

# 动作空间：两步动作（VNF节点映射+链路路径映射）
@dataclass
class OrchestrationAction:
    vnf_node_mapping: Dict[int, int]  # {vnf_id: physical_node_id}
    link_path_mapping: Dict[int, int]  # {virtual_link_id: physical_path_id}