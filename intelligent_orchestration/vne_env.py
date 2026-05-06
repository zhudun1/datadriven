import numpy as np
import gymnasium as gym
from gymnasium import spaces
from common.data_structures import (
    NetworkTopology, PhysicalNode, PhysicalLink, VNR, OrchestrationAction
)
from common.utils import init_logger, calculate_resource_fragmentation
from common.custom_resources import CUSTOM_NODES, CUSTOM_LINKS, CUSTOM_VNR
import pymysql
import json

logger = init_logger()


def load_resources_from_db():
    """从数据库加载物理节点和链路资源（使用规范化新表）"""
    import os
    import re
    MYSQL_HOST = os.environ.get("QOS_MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.environ.get("QOS_MYSQL_PORT", "3306"))
    MYSQL_USER = os.environ.get("QOS_MYSQL_USER", "root")
    MYSQL_PASSWORD = os.environ.get("QOS_MYSQL_PASSWORD", "QosRoot@123")

    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": "intelligent_orchestration", "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }

    nodes = []
    links = []
    node_id_to_int = {}

    try:
        conn = pymysql.connect(**conn_cfg)
        with conn.cursor() as c:
            # 加载物理节点（JOIN多表）
            c.execute("""
                SELECT
                    pn.node_id, pn.node_name, pn.domain, pn.role,
                    nc.cpu_cores, nc.cpu_mips_per_core, nc.cpu_architecture,
                    nc.gpu_cores, nc.gpu_memory_gb, nc.accelerator_type, nc.used_cpu_cores,
                    ns.ram_gb, ns.storage_gb, ns.storage_bandwidth_mbps, ns.used_ram_gb,
                    np.power_idle_w, np.power_per_util_w, np.power_tx_per_mbps_w, np.power_sleep_w,
                    nv.virt_type, nv.max_containers, nv.scheduling_latency_ms
                FROM t_physical_node pn
                LEFT JOIN t_node_compute nc ON pn.node_id = nc.node_id
                LEFT JOIN t_node_storage ns ON pn.node_id = ns.node_id
                LEFT JOIN t_node_power np ON pn.node_id = np.node_id
                LEFT JOIN t_node_virtualization nv ON pn.node_id = nv.node_id
                WHERE pn.is_active = 1
                ORDER BY pn.node_id
            """)
            rows = c.fetchall()
            for i, row in enumerate(rows):
                node_id_str = row["node_id"]
                # 使用数组索引(0-4)而不是解析字符串，避免edge-1和cloud-1冲突
                node_id_int = i
                node_id_to_int[node_id_str] = node_id_int

                total_cpu = row.get("cpu_cores", 8)
                total_memory = row.get("ram_gb", 32)
                used_cpu = row.get("used_cpu_cores", 0) or 0
                used_memory = row.get("used_ram_gb", 0) or 0
                available_cpu = max(0, total_cpu - used_cpu)
                available_memory = max(0, total_memory - used_memory)

                max_cpu, max_memory = 8, 32
                power_idle = row.get("power_idle_w", 50)
                energy_consumption = power_idle / 100.0

                nodes.append({
                    "node_id": node_id_int,
                    "node_id_str": node_id_str,
                    "node_name": row.get("node_name", ""),
                    "domain": row.get("domain", "edge"),
                    "role": row.get("role", "compute"),
                    "cpu": min(available_cpu / max_cpu, 1.0),
                    "memory": min(available_memory / max_memory, 1.0),
                    "energy_consumption": energy_consumption,
                    "total_cpu": total_cpu,
                    "total_memory": total_memory,
                    "used_cpu_cores": used_cpu,
                    "used_memory_gb": used_memory,
                })

            # 加载物理链路
            c.execute("""
                SELECT pl.link_id, pl.link_name, pl.src_node, pl.dst_node,
                    pl.bandwidth_mbps, pl.propagation_delay_ms, pl.queue_policy,
                    pl.max_queue_size, pl.packet_loss_rate, pl.mtbf_hours,
                    pl.tsn_enabled, pl.is_wireless, pl.used_bandwidth_mbps
                FROM t_physical_link pl
                WHERE pl.src_node IS NOT NULL AND pl.dst_node IS NOT NULL
            """)
            for row in c.fetchall():
                src_str, dst_str = row["src_node"], row["dst_node"]
                if src_str in node_id_to_int and dst_str in node_id_to_int:
                    src_int, dst_int = node_id_to_int[src_str], node_id_to_int[dst_str]
                else:
                    continue

                total_bw = row.get("bandwidth_mbps", 1000)
                used_bw = row.get("used_bandwidth_mbps", 0) or 0
                max_bw, max_latency = 40000, 500

                links.append({
                    "link_id": len(links),
                    "link_id_str": row["link_id"],
                    "src_node": src_int,
                    "dst_node": dst_int,
                    "bandwidth": max(0, total_bw - used_bw) / max_bw,
                    "latency": min(row.get("propagation_delay_ms", 5) / max_latency, 1.0),
                    "total_bandwidth": total_bw,
                    "used_bandwidth_mbps": used_bw,
                    "queue_policy": row.get("queue_policy", "FIFO"),
                    "tsn_enabled": bool(row.get("tsn_enabled", 0)),
                })

        conn.close()
        logger.info(f"从数据库加载了 {len(nodes)} 个节点, {len(links)} 条链路（新表结构）")
    except Exception as e:
        logger.warning(f"从数据库加载资源失败: {e}, 使用默认资源")
        nodes = CUSTOM_NODES
        links = CUSTOM_LINKS

    return nodes, links


class GraphVNEEnv(gym.Env):
    metadata = {"render_modes": ["human"]}

    def __init__(self, node_count: int = 5, link_count: int = 8, use_db: bool = True, vnf_count: int = 2):
        super().__init__()

        # 从数据库加载资源
        if use_db:
            db_nodes, db_links = load_resources_from_db()
            self.db_nodes = db_nodes
            self.db_links = db_links
        else:
            self.db_nodes = CUSTOM_NODES
            self.db_links = CUSTOM_LINKS

        self.use_db_mode = use_db  # 标志：是否使用数据库
        self.topology = self._init_topology()
        self.node_count = len(self.db_nodes)
        self.link_count = len(self.db_links)

        # VNF数量可配置
        self.vnf_count = vnf_count

        # 预计算所有节点对之间的最短路径
        self.path_cache = self._get_all_shortest_paths()

        node_feat_dim = 6 * self.node_count  # cpu, mem, energy, degree, used_cpu_cores, used_memory
        link_feat_dim = 3 * self.link_count  # bandwidth, latency, used_bandwidth_mbps
        # VNR特征: 6维QoS向量 (latency, jitter, loss, throughput, reliability, priority)
        # + 1维 vnf_chain比例 = 7维
        vnr_feat_dim = 7
        adj_feat_dim = self.node_count * self.node_count  # 邻接矩阵
        path_cache_dim = self.node_count * self.node_count * 2  # 带宽和延迟缓存
        self.observation_space = spaces.Box(
            low=0.0, high=1.0,
            shape=(node_feat_dim + link_feat_dim + vnr_feat_dim + adj_feat_dim + path_cache_dim,),
            dtype=np.float32
        )

        # VNF数量已在__init__中通过参数传入
        # 动作空间直接选择目标节点（智能体只需选择VNF部署在哪两个节点，系统自动找最优路径）
        action_dims = [self.node_count] * self.vnf_count
        self.action_space = spaces.MultiDiscrete(action_dims, dtype=np.int64)

        # 基础奖励权重
        self.alpha = 100.0  # QoS基础奖励权重
        self.beta = 5.0    # 碎片权重
        self.gamma = 10.0   # 提高能耗权重系数
        self.delta = 30.0  # 约束违反惩罚

    def _init_topology(self) -> NetworkTopology:
        nodes = {}
        for node_cfg in self.db_nodes:
            node = PhysicalNode(
                node_id=node_cfg["node_id"],
                cpu=node_cfg["cpu"],
                memory=node_cfg["memory"],
                energy_onsumption=node_cfg["energy_consumption"],
                used_cpu_cores=node_cfg.get("used_cpu_cores", 0.0),
                used_memory_gb=node_cfg.get("used_memory_gb", 0.0)
            )
            nodes[node_cfg["node_id"]] = node

        links = {}
        adj_matrix = np.zeros((len(self.db_nodes), len(self.db_nodes)))
        for link_cfg in self.db_links:
            link = PhysicalLink(
                link_id=link_cfg["link_id"],
                src_node=link_cfg["src_node"],
                dst_node=link_cfg["dst_node"],
                bandwidth=link_cfg["bandwidth"],
                latency=link_cfg["latency"],
                used_bandwidth_mbps=link_cfg.get("used_bandwidth_mbps", 0.0)
            )
            links[link_cfg["link_id"]] = link
            adj_matrix[link.src_node][link.dst_node] = 1
            adj_matrix[link.dst_node][link.src_node] = 1

        return NetworkTopology(nodes=nodes, links=links, adj_matrix=adj_matrix)

    def _find_path_bw_latency(self, src_node, dst_node):
        """使用BFS找到两点之间的最优路径（最小跳数），返回带宽和延迟"""
        if src_node == dst_node:
            return 1.0, 0.001

        # BFS找最短路径
        visited = {src_node}
        queue = [(src_node, [src_node])]

        while queue:
            current, path = queue.pop(0)
            for link in self.topology.links.values():
                if link.src_node == current and link.dst_node not in visited:
                    new_path = path + [link.dst_node]
                    if link.dst_node == dst_node:
                        # 找到目标节点，计算路径带宽和延迟
                        return self._calc_path_bw_latency(new_path)
                    visited.add(link.dst_node)
                    queue.append((link.dst_node, new_path))
                elif link.dst_node == current and link.src_node not in visited:
                    new_path = path + [link.src_node]
                    if link.src_node == dst_node:
                        return self._calc_path_bw_latency(new_path)
                    visited.add(link.src_node)
                    queue.append((link.src_node, new_path))

        return 0.0, 999.0  # 无法找到路径

    def _calc_path_bw_latency(self, node_path):
        """计算路径的带宽（取最小值）和延迟（取总和）"""
        total_latency = 0.0
        min_bandwidth = 1.0

        for i in range(len(node_path) - 1):
            src, dst = node_path[i], node_path[i + 1]
            for link in self.topology.links.values():
                if (link.src_node == src and link.dst_node == dst) or \
                   (link.src_node == dst and link.dst_node == src):
                    total_latency += link.latency
                    min_bandwidth = min(min_bandwidth, link.bandwidth)
                    break

        return min_bandwidth, total_latency

    def _get_all_shortest_paths(self):
        """预计算所有节点对之间的最短路径信息"""
        path_info = {}
        for i in range(self.node_count):
            for j in range(self.node_count):
                if i != j:
                    bw, lat = self._find_path_bw_latency(i, j)
                    path_info[(i, j)] = {'bandwidth': bw, 'latency': lat}
        return path_info

    def _get_adjacency_info(self) -> np.ndarray:
        """获取节点邻接矩阵信息，作为观察空间的一部分"""
        # 使用邻接矩阵，表示节点间是否直接相连
        return self.topology.adj_matrix.flatten().astype(np.float32)

    def _get_path_connectivity(self) -> np.ndarray:
        """获取每个路径的连通节点对信息"""
        path_info = []
        for path_id in range(self.path_count):
            # 找出该路径包含的所有节点
            nodes_in_path = set()
            for link in self.topology.links.values():
                if link.path_id == path_id:
                    nodes_in_path.add(link.src_node)
                    nodes_in_path.add(link.dst_node)
            # 编码为节点对矩阵
            for i in range(self.node_count):
                for j in range(self.node_count):
                    if i in nodes_in_path and j in nodes_in_path:
                        path_info.append(1.0)
                    else:
                        path_info.append(0.0)
        return np.array(path_info, dtype=np.float32)

    def _get_path_cache_info(self) -> np.ndarray:
        """获取所有节点对之间的带宽和延迟信息"""
        path_feats = []
        for i in range(self.node_count):
            for j in range(self.node_count):
                if (i, j) in self.path_cache:
                    path_feats.append(self.path_cache[(i, j)]['bandwidth'])
                    path_feats.append(self.path_cache[(i, j)]['latency'] / 500.0)  # 归一化延迟
                else:
                    path_feats.append(0.0)
                    path_feats.append(1.0)
        return np.array(path_feats, dtype=np.float32)

    def _get_obs(self) -> np.ndarray:
        node_feats = []
        for node_id, node in self.topology.nodes.items():
            degree = np.sum(self.topology.adj_matrix[node_id]) / self.node_count
            # 归一化used_cpu_cores和used_memory (max: 8 vcpu, 32GB)
            used_cpu_cores_norm = min(node.used_cpu_cores / 8.0, 1.0) if hasattr(node, 'used_cpu_cores') else 0.0
            used_mem_norm = min(node.used_memory_gb / 32.0, 1.0) if hasattr(node, 'used_memory_gb') else 0.0
            node_feats.extend([node.cpu, node.memory, node.energy_onsumption, degree, used_cpu_cores_norm, used_mem_norm])

        link_feats = []
        max_lat = max([link.latency for link in self.topology.links.values()]) if self.topology.links else 1.0
        max_bw = 1.0  # 最大带宽
        for link in self.topology.links.values():
            # 归一化used_bandwidth_mbps
            used_bandwidth_mbps_norm = min(link.used_bandwidth_mbps / max_bw, 1.0) if hasattr(link, 'used_bandwidth_mbps') else 0.0
            link_feats.extend([link.bandwidth, link.latency/max_lat, used_bandwidth_mbps_norm])

        if self.current_vnr:
            # qos_vector: 6维 [latency, jitter, loss, throughput, reliability, priority]
            if len(self.current_vnr.qos_vector) >= 6:
                qos_feats = list(self.current_vnr.qos_vector[:6])
            else:
                # 兼容旧格式
                qos_feats = list(self.current_vnr.qos_vector[:3]) + [0.5, 0.5, 0.5]
            vnr_feats = list(qos_feats) + [len(self.current_vnr.vnf_chain)/self.vnf_count]
        else:
            vnr_feats = [0.0]*7

        # 添加邻接矩阵信息（拓扑连通性）
        adj_info = self._get_adjacency_info()

        # 添加路径缓存信息
        path_cache_info = self._get_path_cache_info()

        return np.array(node_feats + link_feats + vnr_feats + adj_info.tolist() + path_cache_info.tolist(), dtype=np.float32)

    def _calculate_reward(self, action: OrchestrationAction) -> float:
        resource_ok = self._check_resource(action)
        connectivity_ok = self._check_link_connectivity(action)

        logger.info(f"奖励计算: resource_ok={resource_ok}, connectivity_ok={connectivity_ok}")

        # ========== 强化奖励/惩罚 ==========
        # 1. 允许同节点部署（非独占模式）
        # 如果两个VNF部署在同一节点，应该有奖励而不是惩罚
        vnf_nodes = list(action.vnf_node_mapping.values())
        same_node_penalty = 0.0
        if len(vnf_nodes) >= 2:
            # 同节点部署节省链路资源，给予小额奖励
            unique_nodes = len(set(vnf_nodes))
            if unique_nodes == 1:
                logger.info("[same-node] Same-node deployment allowed (non-exclusive mode)")

        if not resource_ok:
            logger.info("资源不足，奖励=-200")
            return -200.0 + same_node_penalty

        if not connectivity_ok:
            logger.info("连通性不足，奖励=-150")
            return -150.0 + same_node_penalty

        # ========== 正向奖励 ==========
        # 2. 鼓励分散部署（VNF部署在不同节点）
        unique_nodes = len(set(vnf_nodes))
        if unique_nodes > 1:
            distribution_reward = 100.0  # 分散部署奖励
        else:
            distribution_reward = 0.0  # 不惩罚也不奖励（如果QoS允许同节点）

        # 3. 正确的连通性奖励
        connectivity_reward = 50.0

        # qos_vector: 6维 [latency, jitter, loss, throughput, reliability, priority]
        # 提取需要的QoS参数
        if len(self.current_vnr.qos_vector) >= 6:
            req_lat = self.current_vnr.qos_vector[0]  # 时延
            req_throughput = self.current_vnr.qos_vector[3]  # 吞吐量
            req_reliability = self.current_vnr.qos_vector[4]  # 可靠性
            req_priority = self.current_vnr.qos_vector[5]  # 优先级
        else:
            # 兼容旧格式
            req_lat = self.current_vnr.qos_vector[0] if len(self.current_vnr.qos_vector) > 0 else 0.5
            req_throughput = 0.5
            req_reliability = 0.5
            req_priority = 0.5

        actual_lat = self._get_actual_path_latency(action)
        actual_bw = self._get_actual_path_bandwidth(action)
        actual_loss = self._get_actual_path_loss(action)

        # 4. QoS匹配奖励 - 进一步优化
        # 时延: 归一化值越小要求越高
        lat_gap = req_lat - actual_lat
        # 时延满足给正奖励，不满足给惩罚
        if lat_gap >= 0:
            lat_score = self.alpha + lat_gap * 200.0  # 增加奖励
        else:
            lat_score = lat_gap * 250.0  # 增加惩罚

        # 吞吐量转换为带宽需求
        req_bw = max(req_throughput, 0.1) / 10.0  # approximate
        bw_gap = actual_bw - req_bw
        # 带宽满足给正奖励，不满足给惩罚
        if bw_gap >= 0:
            bw_score = bw_gap * 100.0  # 增加奖励
        else:
            bw_score = bw_gap * 300.0  # 增加惩罚

        # 丢包率: 根据可靠性等级换算
        # 可靠性越高，丢包率要求越低
        req_loss = (1.0 - req_reliability) * 0.1  # approximate
        loss_gap = req_loss - actual_loss
        if loss_gap >= 0:
            loss_score = 30.0
        else:
            loss_score = loss_gap * 80.0

        qos_total_score = lat_score + bw_score + loss_score

        # 5. 额外奖励：当QoS完全满足时
        qos_bonus = 0.0
        # 检查时延和吞吐量是否满足
        if actual_bw >= req_bw * 0.8 and actual_lat <= req_lat * 1.2:
            qos_bonus = 250.0  # 大幅增加QoS完全满足奖励

        # 6. 探索奖励：鼓励选择不同节点组合（所有组合都加分）
        exploration_reward = 0.0
        node_pair = tuple(sorted(vnf_nodes))
        # 所有节点组合都给予探索奖励，避免总是选择固定组合
        if len(set(vnf_nodes)) > 1:
            # 鼓励不同节点部署 - 大幅提高奖励
            exploration_reward = 80.0
        elif len(vnf_nodes) == 2 and len(set(vnf_nodes)) == 1:
            # 同一节点部署减少奖励，惩罚总是选同一节点
            exploration_reward = -10.0

        energy_penalty = -sum([self.topology.nodes[nid].energy_onsumption for nid in action.vnf_node_mapping.values()]) * self.gamma

        # 负载均衡奖励 - 增大差距让算法更倾向空闲节点
        node_loads = []
        for nid in action.vnf_node_mapping.values():
            if nid in self.topology.nodes:
                node = self.topology.nodes[nid]
                # 使用真实的used_cpu_cores计算负载 (max 8)
                used = getattr(node, 'used_cpu_cores', 0.0)
                node_loads.append(used)
        avg_used = sum(node_loads) / len(node_loads) if node_loads else 0

        # 负载均衡：大幅增加奖励差距
        load_balance_reward = 0.0
        for used in node_loads:
            if used < 1.0:  # 完全空闲 (< 1 vcpu) - 超大奖励
                load_balance_reward += 80.0
            elif used < 2.0:  # 空闲节点 - 大奖励
                load_balance_reward += 40.0
            elif used < 4.0:  # 中等负载
                load_balance_reward += 0.0  # 无奖励
            else:  # 高负载 - 超大惩罚
                load_balance_reward -= 100.0

        # ========== 新增：资源充裕度奖励 ==========
        # 根据节点剩余可用资源计算奖励，可用资源越多奖励越高
        # 这样当边缘满载时，云端资源更充裕 → 自动选择云端
        # 大幅增加奖励差异使学习更明显
        resource_abundance_reward = 0.0
        for vnf_id, node_id in action.vnf_node_mapping.items():
            node = self.topology.nodes.get(node_id)
            if node:
                # 剩余可用资源 = total - used，这里用 cpu 归一化值表示
                available_resource = getattr(node, 'cpu', 0.5)  # cpu 已经是归一化的剩余比例
                # 大幅增加奖励差异: 资源0.1时-80分，资源0.9时+200分 (边缘满时选云端vs选边缘差距400+)
                resource_abundance_reward += (available_resource * 280.0 - 80.0)

        # 优先级参数应用到奖励 (index 2)
        priority = self.current_vnr.qos_vector[2] if len(self.current_vnr.qos_vector) > 2 else 1.0
        priority_multiplier = 1.0 + priority  # 高优先级(1.0)时奖励翻倍

        total_reward = (
            qos_total_score +
            distribution_reward +
            connectivity_reward +
            energy_penalty +
            load_balance_reward * priority_multiplier +
            same_node_penalty +
            qos_bonus +
            exploration_reward +
            resource_abundance_reward  # 新增
        )

        logger.info(f"奖励详情: qos_score={qos_total_score}, distribution={distribution_reward}, connectivity={connectivity_reward}, energy={energy_penalty}, load_balance={load_balance_reward}, same_node_penalty={same_node_penalty}, qos_bonus={qos_bonus}, exploration={exploration_reward}, resource_abundance={resource_abundance_reward}, total={total_reward}")

        return total_reward

    def _get_actual_path_latency(self, action: OrchestrationAction) -> float:
        """使用BFS找到实际路径的延迟"""
        if self.vnf_count < 2 or 1 not in action.vnf_node_mapping:
            return 0.001
        vnf0_node = action.vnf_node_mapping[0]
        vnf1_node = action.vnf_node_mapping[1]

        if vnf0_node == vnf1_node:
            return 0.001  # 同节点几乎无延迟

        # 使用预计算的路径缓存
        if (vnf0_node, vnf1_node) in self.path_cache:
            _, lat = self._find_path_bw_latency(vnf0_node, vnf1_node)
            return lat / 500.0  # 归一化
        return 999.0

    def _get_actual_path_bandwidth(self, action: OrchestrationAction) -> float:
        """使用BFS找到实际路径的带宽"""
        if self.vnf_count < 2 or 1 not in action.vnf_node_mapping:
            return 1.0
        vnf0_node = action.vnf_node_mapping[0]
        vnf1_node = action.vnf_node_mapping[1]

        if vnf0_node == vnf1_node:
            return 1.0  # 同节点带宽充足

        # 使用预计算的路径缓存
        vnf1_node = action.vnf_node_mapping[1]

        if vnf0_node == vnf1_node:
            return 1.0  # 同节点带宽充足

        # 使用预计算的路径缓存
        if (vnf0_node, vnf1_node) in self.path_cache:
            bw, _ = self._find_path_bw_latency(vnf0_node, vnf1_node)
            return bw
        return 0.0

    def _get_actual_path_loss(self, action: OrchestrationAction) -> float:
        if self.vnf_count < 2 or 1 not in action.vnf_node_mapping:
            return 0.001
        if action.vnf_node_mapping[0] == action.vnf_node_mapping[1]:
            return 0.001
        return self._get_actual_path_latency(action) * 0.1

    def _check_qos(self, action: OrchestrationAction) -> bool:
        # 6维QoS: [latency, jitter, loss, throughput, reliability, priority]
        if len(self.current_vnr.qos_vector) >= 6:
            req_lat = self.current_vnr.qos_vector[0]
            req_throughput = self.current_vnr.qos_vector[3]
            req_reliability = self.current_vnr.qos_vector[4]
        else:
            # 兼容旧格式
            req_bw, req_lat, _, req_loss = self.current_vnr.qos_vector[:4]
            req_throughput = 0.5
            req_reliability = 0.5

        actual_lat = self._get_actual_path_latency(action)
        actual_bw = self._get_actual_path_bandwidth(action)

        # 简化检查: 吞吐量转换为带宽需求
        req_bw = max(req_throughput, 0.1) / 10.0

        return (actual_bw >= req_bw) and (actual_lat <= req_lat * 1.2)

    def _check_resource(self, action: OrchestrationAction) -> bool:
        """检查资源是否足够 - 使用实际used_cpu_cores，支持非独占模式"""
        node_demands = {}
        for vnf_id, node_id in action.vnf_node_mapping.items():
            # 边界检查: 确保节点ID在有效范围内
            if node_id not in self.topology.nodes:
                logger.warning(f"[resource] Invalid node_id: {node_id}, valid range: 0-{len(self.topology.nodes)-1}")
                return False

            # 每个VNF需要的归一化资源（0-1之间）
            cpu_demand = self.current_vnr.vnf_cpu_emand[vnf_id] if vnf_id < len(self.current_vnr.vnf_cpu_emand) else 0.1
            node_demands[node_id] = node_demands.get(node_id, 0.0) + cpu_demand

        for node_id, total_demand in node_demands.items():
            # 使用实际used_cpu_cores计算（max 8）
            node = self.topology.nodes[node_id]
            used = getattr(node, 'used_cpu_cores', 0.0)
            # 节点可用 = 1 - used/8
            available = 1.0 - (used / 8.0)
            if available < total_demand:
                logger.warning(f"[resource] Node {node_id} insufficient: need={total_demand:.2f}, have={available:.2f} ({used:.1f}/8 used)")
                return False
        return True

    def _check_link_connectivity(self, action: OrchestrationAction) -> bool:
        # 动态检查：只有vnf_count>1时才检查链接连通性
        if self.vnf_count < 2:
            logger.info("vnf_count<2, 跳过连通性检查")
            return True

        vnf_keys = list(action.vnf_node_mapping.keys())
        if len(vnf_keys) < 2:
            return True

        vnf0_node = action.vnf_node_mapping[0]
        if 1 not in action.vnf_node_mapping:
            return True
        vnf1_node = action.vnf_node_mapping[1]

        logger.info(f"连通性检查: vnf0={vnf0_node}, vnf1={vnf1_node}")

        # 如果两个VNF部署在同一节点，连通性满足
        if vnf0_node == vnf1_node:
            logger.info("同节点部署，连通性满足")
            return True

        # 使用BFS检查两个节点是否连通（通过任意中间节点）
        visited = set()
        queue = [vnf0_node]
        visited.add(vnf0_node)

        while queue:
            current = queue.pop(0)
            if current == vnf1_node:
                logger.info(f"节点 {vnf0_node} 到 {vnf1_node} 连通")
                return True
            for neighbor in range(self.node_count):
                if self.topology.adj_matrix[current][neighbor] == 1 and neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        logger.info(f"节点 {vnf0_node} 到 {vnf1_node} 不连通")
        return False

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)

        # ========== 功能6：资源动态变化训练场景 ==========
        # 随机选择不同的资源状态场景，覆盖真实运行中的各种情况
        resource_scenario = np.random.randint(0, 6)

        if resource_scenario == 0:
            # 场景0：所有节点资源充足（含边缘）
            for node in self.topology.nodes.values():
                node.cpu = np.random.uniform(0.7, 0.95)
                node.memory = np.random.uniform(0.7, 0.95)
        elif resource_scenario == 1:
            # 场景1：边缘满载，云端充足（需要选云端）
            for node in self.topology.nodes.values():
                if node.node_id < 3:  # 边缘节点 0,1,2
                    node.cpu = np.random.uniform(0.05, 0.2)  # 边缘满载
                    node.memory = np.random.uniform(0.05, 0.2)
                else:  # 云端节点 3,4
                    node.cpu = np.random.uniform(0.7, 0.95)
                    node.memory = np.random.uniform(0.7, 0.95)
        elif resource_scenario == 2:
            # 场景2：边缘部分满，需要选择
            for node in self.topology.nodes.values():
                if node.node_id < 3:  # 边缘节点
                    node.cpu = np.random.uniform(0.3, 0.5)  # 部分满
                    node.memory = np.random.uniform(0.3, 0.5)
                else:  # 云端节点
                    node.cpu = np.random.uniform(0.5, 0.8)
                    node.memory = np.random.uniform(0.5, 0.8)
        elif resource_scenario == 3:
            # 场景3：云端满载，边缘充足
            for node in self.topology.nodes.values():
                if node.node_id >= 3:  # 云端节点
                    node.cpu = np.random.uniform(0.05, 0.2)  # 云端满
                    node.memory = np.random.uniform(0.05, 0.2)
                else:  # 边缘节点
                    node.cpu = np.random.uniform(0.7, 0.95)
                    node.memory = np.random.uniform(0.7, 0.95)
        elif resource_scenario == 4:
            # 场景4：所有节点都紧张（高负载）
            for node in self.topology.nodes.values():
                node.cpu = np.random.uniform(0.1, 0.3)
                node.memory = np.random.uniform(0.1, 0.3)
        else:
            # 场景5：随机状态（原有逻辑）
            for node in self.topology.nodes.values():
                node.cpu = np.random.uniform(0.2, 0.95)
                node.memory = np.random.uniform(0.2, 0.95)

        # 链路带宽随机
        for link in self.topology.links.values():
            link.bandwidth = np.random.uniform(0.3, 1.5)

        # ========== QoS生成: 支持两种模式 ==========
        # 模式A: 外部输入 (options带参数) → 调用翻译器
        # 模式B: 随机生成 (向后兼容)
        if options and 'anomaly_score' in options:
            # 模式A: 使用外部输入的QoS参数
            anomaly_score = options['anomaly_score']
            data_size_mb = options.get('data_size_mb', 1.0)
            data_type = options.get('data_type', 'image')
            business_scenario = options.get('business_scenario')

            try:
                from business_perception.qos_translator import get_qos_translator
                translator = get_qos_translator()
                _, qos_vector, details = translator.translate(
                    anomaly_score, data_size_mb, data_type, business_scenario
                )
                logger.info(f"QoS翻译(外部输入): anomaly={anomaly_score}, "
                          f"size={data_size_mb}MB → {details.get('anomaly_level')}")
            except Exception as e:
                logger.warning(f"QoS翻译失败，使用随机QoS: {e}")
                qos_scenarios = [
                    [0.02, 0.8, 0.0, 0.8, 0.4, 0.3],
                    [0.2, 0.1, 0.33, 0.5, 0.6, 0.5],
                    [0.5, 0.04, 0.66, 0.25, 0.8, 0.7],
                    [0.8, 0.02, 1.0, 0.05, 1.0, 0.9]
                ]
                qos_vector = np.array(qos_scenarios[np.random.randint(0, 4)], dtype=np.float32)
        else:
            # 模式B: 随机生成 (向后兼容)
            qos_scenarios = [
                [0.02, 0.8, 0.0, 0.8, 0.4, 0.3],
                [0.2, 0.1, 0.33, 0.5, 0.6, 0.5],
                [0.5, 0.04, 0.66, 0.25, 0.8, 0.7],
                [0.8, 0.02, 1.0, 0.05, 1.0, 0.9]
            ]
            qos_vector = np.array(qos_scenarios[np.random.randint(0, 4)], dtype=np.float32)

        # 先初始化VNR（确保_get_obs返回正确维度）
        self.current_vnr = VNR(
            vnf_chain=CUSTOM_VNR["vnf_chain"],
            qos_vector=np.array(qos_vector, dtype=np.float32),
            vnf_cpu_emand=CUSTOM_VNR["vnf_cpu_demand"],
            vnf_mem_emand=CUSTOM_VNR["vnf_mem_demand"],
            link_bw_emand=CUSTOM_VNR["link_bw_demand"]
        )

        logger.info(f"环境重置: 场景={resource_scenario}, QoS={qos_vector.tolist()}, 节点资源={[round(n.cpu,2) for n in self.topology.nodes.values()]}")

        # stable_baselines3兼容: 返回(obs, info)元组
        return self._get_obs(), {"topology": self.topology, "vnr": self.current_vnr}

    def step(self, action):
        # 每次编排前重新加载最新资源状态（解决并发冲突）
        self._reload_resources()

        action_dict = action if isinstance(action, dict) else self._unflatten_action(action)

        # 裁剪动作到有效范围
        vnf_nodes = action_dict.get("vnf_node", [])

        # 节点范围: 0 到 node_count-1
        clipped_vnf_nodes = [max(0, min(n, self.node_count - 1)) for n in vnf_nodes]

        action_dict["vnf_node"] = clipped_vnf_nodes

        logger.info(f"裁剪后动作: vnf_node={clipped_vnf_nodes}")

        # 动作只包含VNF节点映射，链路通过BFS自动计算
        orchestration_action = OrchestrationAction(
            vnf_node_mapping={i: node for i, node in enumerate(action_dict["vnf_node"])},
            link_path_mapping={}  # 不再需要手动指定链路
        )

        # 计算实际使用的链路路径
        link_path = self._compute_link_path(orchestration_action)
        orchestration_action.link_path_mapping = link_path

        reward = self._calculate_reward(orchestration_action)
        qos_ok = self._check_qos(orchestration_action)
        res_ok = self._check_resource(orchestration_action)

        # 分配节点资源
        if res_ok:
            for vnf_id, node_id in orchestration_action.vnf_node_mapping.items():
                self.topology.nodes[node_id].cpu -= self.current_vnr.vnf_cpu_emand[vnf_id]

        # 分配链路资源（从链路带宽中扣除）
        if res_ok and link_path:
            for link_idx, link_info in link_path.items():
                link_id = link_info.get("link_id")
                if link_id is not None and link_id in self.topology.links:
                    link = self.topology.links[link_id]
                    link_bw_demand = self.current_vnr.link_bw_emand[0] if self.current_vnr.link_bw_emand else 0.1
                    link.bandwidth = max(0, link.bandwidth - link_bw_demand)

        return self._get_obs(), reward, True, False, {
            "qos_ok": qos_ok,
            "resource_ok": res_ok,
            "link_path": link_path
        }

    def _unflatten_action(self, action_flat: np.ndarray) -> dict:
        # 新的动作格式：只有VNF节点
        vnf_node = action_flat[:self.vnf_count].tolist()
        return {"vnf_node": vnf_node}

    def set_qos_to_vnr(self, qos_vector: np.ndarray):
        self.current_vnr.qos_vector = qos_vector
        # 确保link_bw_demand也存在
        if not self.current_vnr.link_bw_emand:
            self.current_vnr.link_bw_emand = CUSTOM_VNR.get("link_bw_demand", [0.2])

    def set_qos_input(self, anomaly_score: float, data_size_mb: float,
                 data_type: str = "image", business_scenario: str = None):
        """设置外部QoS输入，通过翻译器生成6维QoS向量

        Args:
            anomaly_score: 异常分数 (0-1)
            data_size_mb: 数据大小(MB)
            data_type: 数据类型 (image/video/pointcloud)
            business_scenario: 业务场景 (safety/quality/maintenance/monitoring)
        """
        try:
            from business_perception.qos_translator import get_qos_translator
            translator = get_qos_translator()
            _, qos_vector, details = translator.translate(
                anomaly_score, data_size_mb, data_type, business_scenario
            )
            logger.info(f"QoS翻译: anomaly={anomaly_score}, size={data_size}MB, "
                      f"scenario={business_scenario} → {details}")
            self.set_qos_to_vnr(qos_vector)
        except Exception as e:
            logger.warning(f"QoS翻译失败，使用默认QoS: {e}")
            # 回退到默认QoS
            default_qos = np.array([0.5, 0.1, 0.5, 0.5, 0.5, 0.5], dtype=np.float32)
            self.set_qos_to_vnr(default_qos)

    def _compute_link_path(self, action: OrchestrationAction) -> dict:
        """计算VNF之间的实际链路路径"""
        link_path = {}
        vnf_nodes = list(action.vnf_node_mapping.values())

        if len(vnf_nodes) < 2:
            return link_path

        for i in range(len(vnf_nodes) - 1):
            src_node = vnf_nodes[i]
            dst_node = vnf_nodes[i + 1]

            # 使用BFS找路径
            path = self._find_path_bw_latency(src_node, dst_node)
            bw, latency = path
            logger.info(f"_compute_link_path: {src_node} -> {dst_node}, bw={bw}, latency={latency}")

            if bw > 0:  # 带宽大于0表示找到路径
                # 找到实际连接这两个节点的链路ID
                link_id = None
                for link in self.topology.links.values():
                    src = str(link.src_node) if link.src_node else ''
                    dst = str(link.dst_node) if link.dst_node else ''
                    # 直接比较
                    if (src == src_node and dst == dst_node) or (src == dst_node and dst == src_node):
                        link_id = link.link_id
                        logger.info(f"_compute_link_path: found link_id={link_id} for {src}->{dst}")
                        break

                # 记录链路信息：包含带宽和延迟
                link_path[i] = {
                    "src": src_node,
                    "dst": dst_node,
                    "link_id": link_id,
                    "bandwidth": round(bw, 2),
                    "latency": round(latency, 2) if latency < 999 else 0
                }
                logger.info(f"_compute_link_path: link_path[{i}]={link_path[i]}")

        return link_path

    def _reload_resources(self):
        """每次编排前重新加载最新资源状态（解决并发冲突）"""
        # 根据初始化模式选择资源来源
        if self.use_db_mode:
            db_nodes, db_links = load_resources_from_db()
            self.db_nodes = db_nodes
            self.db_links = db_links
        # 重新初始化拓扑
        self.topology = self._init_topology()
        # 重新计算路径缓存
        self.path_cache = self._get_all_shortest_paths()