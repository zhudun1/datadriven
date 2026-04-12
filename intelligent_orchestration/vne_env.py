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
    """从数据库加载物理节点和链路资源"""
    import os
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

    try:
        conn = pymysql.connect(**conn_cfg)
        with conn.cursor() as c:
            # 加载计算节点
            c.execute("SELECT * FROM t_resource_inventory WHERE resource_type='compute' AND is_active=1")
            for row in c.fetchall():
                state = row.get("current_state", {})
                if isinstance(state, str):
                    state = json.loads(state)
                nodes.append({
                    "node_id": int(row["resource_id"].split("-")[1]),
                    "cpu": state.get("cpu", 0.5),
                    "memory": state.get("memory", 0.5),
                    "energy_consumption": state.get("energy_consumption", 0.5)
                })

            # 加载网络链路
            c.execute("SELECT * FROM t_resource_inventory WHERE resource_type='network' AND is_active=1")
            for row in c.fetchall():
                state = row.get("current_state", {})
                if isinstance(state, str):
                    state = json.loads(state)
                links.append({
                    "link_id": int(row["resource_id"].split("-")[1]),
                    "src_node": state.get("src", 0),
                    "dst_node": state.get("dst", 0),
                    "bandwidth": state.get("bandwidth", 1.0),
                    "latency": state.get("latency", 10),
                    "path_id": state.get("path_id", 0)
                })
        conn.close()
        logger.info(f"从数据库加载了 {len(nodes)} 个节点, {len(links)} 条链路")
    except Exception as e:
        logger.warning(f"从数据库加载资源失败: {e}, 使用默认资源")
        nodes = CUSTOM_NODES
        links = CUSTOM_LINKS

    return nodes, links


class GraphVNEEnv(gym.Env):
    metadata = {"render_modes": ["human"]}

    def __init__(self, node_count: int = 5, link_count: int = 8, use_db: bool = True):
        super().__init__()

        # 从数据库加载资源
        if use_db:
            db_nodes, db_links = load_resources_from_db()
            self.db_nodes = db_nodes
            self.db_links = db_links
        else:
            self.db_nodes = CUSTOM_NODES
            self.db_links = CUSTOM_LINKS

        self.topology = self._init_topology()
        self.node_count = len(self.db_nodes)
        self.link_count = len(self.db_links)

        # 预计算所有节点对之间的最短路径
        self.path_cache = self._get_all_shortest_paths()

        node_feat_dim = 4 * self.node_count  # cpu, mem, energy, degree
        link_feat_dim = 2 * self.link_count
        vnr_feat_dim = 5
        adj_feat_dim = self.node_count * self.node_count  # 邻接矩阵
        path_cache_dim = self.node_count * self.node_count * 2  # 带宽和延迟缓存
        self.observation_space = spaces.Box(
            low=0.0, high=1.0,
            shape=(node_feat_dim + link_feat_dim + vnr_feat_dim + adj_feat_dim + path_cache_dim,),
            dtype=np.float32
        )

        self.vnf_count = 2
        # 动作空间改为直接选择目标节点（智能体只需选择VNF部署在哪两个节点，系统自动找最优路径）
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
                energy_consumption=node_cfg["energy_consumption"]
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
                path_id=link_cfg["path_id"]
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
            node_feats.extend([node.cpu, node.memory, node.energy_consumption, degree])

        link_feats = []
        max_lat = max([link.latency for link in self.topology.links.values()]) if self.topology.links else 1.0
        for link in self.topology.links.values():
            link_feats.extend([link.bandwidth, link.latency/max_lat])

        if self.current_vnr:
            vnr_feats = list(self.current_vnr.qos_vector) + [len(self.current_vnr.vnf_chain)/self.vnf_count]
        else:
            vnr_feats = [0.0]*5

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
        # 1. 严重惩罚同节点部署（如果VNF之间需要通信）
        vnf_nodes = list(action.vnf_node_mapping.values())
        same_node_penalty = 0.0
        if len(vnf_nodes) >= 2:
            # 如果QoS要求带宽>0，说明VNF间需要通信，同节点部署会失去意义
            req_bw = self.current_vnr.qos_vector[0]
            if req_bw > 0.05 and len(set(vnf_nodes)) == 1:
                same_node_penalty = -300.0  # 严重惩罚同节点部署
                logger.info(f"同节点部署惩罚: {same_node_penalty}")

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

        req_bw, req_lat, req_prio, req_loss = self.current_vnr.qos_vector
        actual_lat = self._get_actual_path_latency(action)
        actual_bw = self._get_actual_path_bandwidth(action)
        actual_loss = self._get_actual_path_loss(action)

        # 4. QoS匹配奖励 - 进一步优化
        lat_gap = req_lat - actual_lat
        # 时延满足给正奖励，不满足给惩罚
        if lat_gap >= 0:
            lat_score = self.alpha + lat_gap * 200.0  # 增加奖励
        else:
            lat_score = lat_gap * 250.0  # 增加惩罚

        bw_gap = actual_bw - req_bw
        # 带宽满足给正奖励，不满足给惩罚
        if bw_gap >= 0:
            bw_score = bw_gap * 100.0  # 增加奖励
        else:
            bw_score = bw_gap * 300.0  # 增加惩罚

        # 丢包率匹配
        loss_gap = req_loss - actual_loss
        if loss_gap >= 0:
            loss_score = 30.0
        else:
            loss_score = loss_gap * 80.0

        qos_total_score = lat_score + bw_score + loss_score

        # 5. 额外奖励：当QoS完全满足时
        qos_bonus = 0.0
        if actual_bw >= req_bw and actual_lat <= req_lat and actual_loss <= req_loss:
            qos_bonus = 250.0  # 大幅增加QoS完全满足奖励

        # 6. 探索奖励：鼓励选择不同节点组合
        exploration_reward = 0.0
        node_pair = tuple(sorted(vnf_nodes))
        # 常见优质节点组合奖励
        good_pairs = [(0, 1), (0, 3), (1, 3), (2, 3)]
        if node_pair in good_pairs:
            exploration_reward = 50.0

        energy_penalty = -sum([self.topology.nodes[nid].energy_consumption for nid in action.vnf_node_mapping.values()]) * self.gamma

        # 负载均衡奖励
        node_loads = []
        for nid in action.vnf_node_mapping.values():
            if nid in self.topology.nodes:
                node_loads.append(self.topology.nodes[nid].cpu)
        avg_load = sum(node_loads) / len(node_loads) if node_loads else 0
        load_balance_reward = -avg_load * 15.0

        total_reward = (
            qos_total_score +
            distribution_reward +
            connectivity_reward +
            energy_penalty +
            load_balance_reward +
            same_node_penalty +
            qos_bonus +
            exploration_reward
        )

        logger.info(f"奖励详情: qos_score={qos_total_score}, distribution={distribution_reward}, connectivity={connectivity_reward}, energy={energy_penalty}, load_balance={load_balance_reward}, same_node_penalty={same_node_penalty}, qos_bonus={qos_bonus}, exploration={exploration_reward}, total={total_reward}")

        return total_reward

    def _get_actual_path_latency(self, action: OrchestrationAction) -> float:
        """使用BFS找到实际路径的延迟"""
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
        vnf0_node = action.vnf_node_mapping[0]
        vnf1_node = action.vnf_node_mapping[1]

        if vnf0_node == vnf1_node:
            return 1.0  # 同节点带宽充足

        # 使用预计算的路径缓存
        if (vnf0_node, vnf1_node) in self.path_cache:
            bw, _ = self._find_path_bw_latency(vnf0_node, vnf1_node)
            return bw
        return 0.0

    def _get_actual_path_loss(self, action: OrchestrationAction) -> float:
        if action.vnf_node_mapping[0] == action.vnf_node_mapping[1]:
            return 0.001 
        return self._get_actual_path_latency(action) * 0.1

    def _check_qos(self, action: OrchestrationAction) -> bool:
        req_bw, req_lat, _, req_loss = self.current_vnr.qos_vector
        actual_lat = self._get_actual_path_latency(action)
        actual_bw = self._get_actual_path_bandwidth(action)
        actual_loss = self._get_actual_path_loss(action)
        return (actual_bw >= req_bw) and (actual_lat <= req_lat) and (actual_loss <= req_loss)

    def _check_resource(self, action: OrchestrationAction) -> bool:
        node_demands = {}
        for vnf_id, node_id in action.vnf_node_mapping.items():
            node_demands[node_id] = node_demands.get(node_id, 0.0) + self.current_vnr.vnf_cpu_demand[vnf_id]
        
        for node_id, total_demand in node_demands.items():
            if self.topology.nodes[node_id].cpu < total_demand:
                return False
        return True

    def _check_link_connectivity(self, action: OrchestrationAction) -> bool:
        vnf0_node = action.vnf_node_mapping[0]
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
        # 降低资源初始下限 (0.3)，增加资源竞争压力，迫使智能体无法永远在单节点塞下所有VNF
        # 每次重置时随机化资源分布，增加探索多样性
        for node in self.topology.nodes.values():
            node.cpu = np.random.uniform(0.2, 0.95)
            node.memory = np.random.uniform(0.2, 0.95)
        for link in self.topology.links.values():
            link.bandwidth = np.random.uniform(0.3, 1.5)

        # 多样化 QoS 场景
        qos_scenarios = [
            [0.02, 0.8, 0.0, 0.8],   # NORMAL - 高带宽，低优先级
            [0.2, 0.1, 0.33, 0.5],   # MEDIUM
            [0.5, 0.04, 0.66, 0.25], # HIGH - 低带宽，高优先级
            [0.8, 0.02, 1.0, 0.05]   # CRITICAL - 极低带宽要求
        ]
        random_qos = qos_scenarios[np.random.randint(0, 4)]

        self.current_vnr = VNR(
            vnf_chain=CUSTOM_VNR["vnf_chain"],
            qos_vector=np.array(random_qos, dtype=np.float32),
            vnf_cpu_demand=CUSTOM_VNR["vnf_cpu_demand"],
            vnf_mem_demand=CUSTOM_VNR["vnf_mem_demand"],
            link_bw_demand=CUSTOM_VNR["link_bw_demand"]
        )

        logger.info(f"环境重置: QoS={random_qos}, 节点资源={[round(n.cpu,2) for n in self.topology.nodes.values()]}")

        return self._get_obs(), {"topology": self.topology, "vnr": self.current_vnr}

    def step(self, action):
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

        reward = self._calculate_reward(orchestration_action)
        qos_ok = self._check_qos(orchestration_action)
        res_ok = self._check_resource(orchestration_action)

        if res_ok:
            for vnf_id, node_id in orchestration_action.vnf_node_mapping.items():
                self.topology.nodes[node_id].cpu -= self.current_vnr.vnf_cpu_demand[vnf_id]

        return self._get_obs(), reward, True, False, {
            "qos_ok": qos_ok,
            "resource_ok": res_ok
        }

    def _unflatten_action(self, action_flat: np.ndarray) -> dict:
        # 新的动作格式：只有VNF节点
        vnf_node = action_flat[:self.vnf_count].tolist()
        return {"vnf_node": vnf_node}

    def set_qos_to_vnr(self, qos_vector: np.ndarray):
        self.current_vnr.qos_vector = qos_vector