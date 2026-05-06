import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional

# ========================================================
# 物理节点数据结构（多维属性模型）
# ========================================================

@dataclass
class ComputeResource:
    """计算资源：CPU/GPU/NPU"""
    cpu_cores: int = 8              # CPU核心数
    cpu_mips_per_core: int = 3000   # 每核MIPS（百万指令/秒）
    cpu_architecture: str = "x86_64" # x86/ARM/RISC-V
    gpu_cores: int = 0              # CUDA核心数（无GPU为0）
    gpu_memory_gb: float = 0.0       # GPU显存
    accelerator_type: str = "None"   # NPU/FPGA/None

    def total_mips(self) -> int:
        return self.cpu_cores * self.cpu_mips_per_core

    def has_gpu(self) -> bool:
        return self.gpu_cores > 0

    def gpu_tflops(self) -> float:
        """估算GPU TFLOPS（粗略估算，无精确数据时返回0）"""
        if self.gpu_cores <= 0:
            return 0.0
        return self.gpu_cores * 0.5 / 1000.0  # 估算值


@dataclass
class StorageResource:
    """存储资源：RAM/磁盘/读写带宽"""
    ram_gb: float = 32.0            # 可用内存
    storage_gb: float = 500.0       # 本地磁盘/SSD
    storage_bandwidth_mbps: float = 200.0  # 存储读写带宽


@dataclass
class NetworkInterface:
    """单个网络接口"""
    interface_id: int = 0
    iface_type: str = "Ethernet"       # Ethernet/Wi-Fi/5G
    mac: str = ""
    ip: str = ""
    supported_protocols: List[str] = field(default_factory=lambda: ["HTTP", "gRPC"])
    max_tx_power_dbm: float = 30.0  # 无线发射功率
    tsn_capable: bool = False       # 是否支持TSN


@dataclass
class PowerModel:
    """能耗模型"""
    power_idle_w: float = 50.0        # 空闲功耗（W）
    power_per_util_w: float = 0.5      # 每1%CPU利用率的功耗上升斜率（W/%）
    power_tx_per_mbps_w: float = 0.01    # 每Mbps发射增加的功耗（W/Mbps）
    power_sleep_w: float = 2.0         # 休眠功耗（W）

    def power_at_util(self, util_percent: float) -> float:
        """估算某CPU利用率下的功耗"""
        return self.power_idle_w + util_percent * self.power_per_util_w

    def energy_factor(self) -> float:
        """归一化能耗因子（用于PPO奖励），值越高越耗能"""
        return self.power_idle_w / 100.0  # 归一化到 0~1.5 范围


@dataclass
class VirtualizationCapability:
    """虚拟化与部署能力"""
    virt_type: str = "docker"             # docker/kvm/k3s
    max_containers: int = 8               # 同时运行容器数上限
    scheduling_latency_ms: float = 500.0  # 冷启动时间（ms）


# 物理节点数据结构
@dataclass
class PhysicalNode:
    node_id: int
    node_name: str = ""

    # 层级/角色
    domain: str = "edge"      # field/edge/cloud
    role: str = "compute"      # compute/switch/camera/plc/agv/sensor

    # 五大资源类
    compute: ComputeResource = field(default_factory=ComputeResource)
    storage: StorageResource = field(default_factory=StorageResource)
    power: PowerModel = field(default_factory=PowerModel)
    virt_cap: VirtualizationCapability = field(default_factory=VirtualizationCapability)
    interfaces: List[NetworkInterface] = field(default_factory=list)

    # 归一化属性（用于PPO动作空间）
    cpu: float = 1.0          # 归一化CPU剩余比例（0-1）
    memory: float = 1.0         # 归一化内存剩余比例（0-1）
    energy_onsumption: float = 0.5  # 归一化能耗因子（0-1）

    # 使用状态（实际占用值）
    used_cpu_cores: float = 0.0   # 已使用CPU核数
    used_memory_gb: float = 0.0    # 已使用内存GB

    # 可用计算（用于资源检查）
    def available_cpu(self, max_cores: float = 8.0) -> float:
        return max(0.0, max_cores - self.used_cpu_cores) / max_cores

    def available_memory(self, max_gb: float = 32.0) -> float:
        return max(0.0, max_gb - self.used_memory_gb) / max_gb

    def get_interface(self, iface_type: str = "Ethernet") -> Optional[NetworkInterface]:
        for iface in self.interfaces:
            if iface.iface_type == iface_type:
                return iface
        return None

    def tsn_capable(self) -> bool:
        return any(iface.tsn_capable for iface in self.interfaces)


# ========================================================
# 物理链路数据结构（多维属性模型）
# ========================================================

@dataclass
class TSNConfig:
    """TSN配置（802.1Qbv等）"""
    gate_control_list: str = ""           # 门控列表配置（如 "8,8,8,8,8"）
    stream_id: str = ""
    traffic_class: int = 0               # 流量类别
    max_latency_ns: int = 0              # 最大时延（ns）


@dataclass
class WirelessModel:
    """无线链路模型"""
    rssi_model: str = "free_space"       # RSSI计算模型标识
    sinr_model: str = "constant"         # SINR计算模型
    mobility: bool = False                # 节点是否移动
    handover_latency_ms: float = 0.0    # AP切换时延

    def rssi_at_distance(self, distance_m: float) -> float:
        """估算RSSI（dBm），自由空间模型"""
        import math
        if distance_m <= 0:
            return -30.0
        pl = 20 * math.log10(distance_m) + 20 * math.log10(5.0) + 32.44
        return max(-100.0, -30.0 - pl)


# 物理链路数据结构
@dataclass
class PhysicalLink:
    link_id: str
    src_node: str
    dst_node: str

    # 路径ID（可选，用于路径分类）
    path_id: int = 0

    # 有线链路属性
    bandwidth_mbps: float = 1000.0      # 链路带宽上限
    propagation_delay_ms: float = 5.0    # 传播时延（固定）
    queue_policy: str = "FIFO"           # FIFO/SP/TAS
    max_queue_size: int = 1024          # 缓冲区大小（包数）
    packet_loss_rate: float = 0.0       # 随机丢包率（%）
    mtbf_hours: float = 87600.0         # 平均无故障时间（h）

    # TSN
    tsn_enabled: bool = False
    tsn_config: Optional[TSNConfig] = None

    # 无线扩展
    is_wireless: bool = False
    wireless_model: Optional[WirelessModel] = None

    # 状态
    used_bandwidth_mbps: float = 0.0       # 已使用带宽

    # 用于PPO的归一化属性
    bandwidth: float = 1.0               # 归一化带宽（0-1）
    latency: float = 0.01                # 归一化延迟

    def available_bandwidth(self) -> float:
        return max(0.0, self.bandwidth_mbps - self.used_bandwidth_mbps)

    def queue_depth_ms(self) -> float:
        """队列排队时延估算（ms）"""
        if self.bandwidth_mbps <= 0:
            return 0.0
        return self.max_queue_size * 1500 * 8.0 / (self.bandwidth_mbps * 1000.0)

    def availability(self) -> float:
        """年可用度（%）"""
        if self.mtbf_hours <= 0:
            return 100.0
        return 99.9  # 简化估算


# ========================================================
# 数据流模型（业务请求）
# ========================================================

@dataclass
class BurstPattern:
    """突发特性"""
    duration_s: float = 10.0        # 持续时间（s）
    peak_bandwidth_mbps: float = 200.0  # 峰值带宽


@dataclass
class DataFlow:
    """数据流请求（编排触发来源）"""
    flow_id: str = ""
    source_node: str = ""           # 源节点ID
    destination_node: str = ""      # 目标节点ID（Edge-1 / Cloud-1）
    data_type: str = "image"         # image/video/pointcloud/text
    data_volume_mb: float = 0.0    # 本次请求数据总量
    deadline_ms: float = 100.0      # 端到端时延上限
    reliability: float = 0.9999    # 丢包容忍度
    priority: int = 3              # 1~7（类似802.1p）
    periodicity: bool = False       # 周期性/突发性
    burst: Optional[BurstPattern] = None

    def is_periodic(self) -> bool:
        return self.periodicity

    def qos_level(self) -> str:
        if self.deadline_ms <= 10 and self.reliability >= 0.99999:
            return "critical"
        elif self.deadline_ms <= 50:
            return "high"
        elif self.deadline_ms <= 200:
            return "medium"
        return "normal"


# ========================================================
# 虚拟网络请求（VNF链+QoS向量）
# ========================================================

@dataclass
class VNR:
    vnf_chain: List[int]                      # VNF链 [vnf1_id, vnf2_id, ...]
    qos_vector: np.ndarray                    # [带宽, 时延, 优先级, 丢包率]（归一化0-1）
    vnf_cpu_emand: List[float] = field(default_factory=list)    # 每个VNF的CPU需求
    vnf_mem_emand: List[float] = field(default_factory=list)  # 每个VNF的内存需求
    link_bw_emand: List[float] = field(default_factory=list)  # 每条虚拟链路的带宽需求

    def total_cpu_demand(self) -> float:
        return sum(self.vnf_cpu_emand)

    def total_mem_demand(self) -> float:
        return sum(self.vnf_mem_emand)


# ========================================================
# 图结构拓扑（节点+链路）
# ========================================================

@dataclass
class NetworkTopology:
    nodes: Dict[int, PhysicalNode]  # {node_id: PhysicalNode}
    links: Dict[int, PhysicalLink]   # {link_id: PhysicalLink}
    adj_matrix: np.ndarray           # 邻接矩阵（节点数×节点数）


# ========================================================
# 动作空间：两步动作（VNF节点映射+链路路径映射）
# ========================================================

@dataclass
class OrchestrationAction:
    vnf_node_mapping: Dict[int, int]   # {vnf_id: physical_node_id}
    link_path_mapping: Dict[int, int]  # {virtual_link_id: physical_path_id}