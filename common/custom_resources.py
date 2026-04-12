# common/custom_resources.py
# 自定义物理节点资源（5个节点，可增删）
# custom_resources.py

CUSTOM_NODES = [
    {"node_id": 0, "cpu": 0.6, "memory": 0.9, "energy_consumption": 0.2},
    {"node_id": 1, "cpu": 0.8, "memory": 0.75, "energy_consumption": 0.35},
    {"node_id": 2, "cpu": 0.7, "memory": 0.5, "energy_consumption": 0.5},
    {"node_id": 3, "cpu": 0.95, "memory": 0.9, "energy_consumption": 0.25}, 
    {"node_id": 4, "cpu": 0.5, "memory": 0.3, "energy_consumption": 0.8}
]

# common/custom_resources.py

CUSTOM_LINKS = [
    # --- 路径 0 (主要服务于 节点 0, 1, 3 之间的连接) ---
    {"link_id": 0, "src_node": 0, "dst_node": 1, "bandwidth": 1.2, "latency": 5, "path_id": 0}, 
    {"link_id": 1, "src_node": 1, "dst_node": 3, "bandwidth": 1.1, "latency": 10, "path_id": 0},
    # --- 路径 1 (主要服务于 节点 1, 2, 3 之间的连接) ---
    {"link_id": 2, "src_node": 1, "dst_node": 2, "bandwidth": 0.8, "latency": 30, "path_id": 1},
    {"link_id": 3, "src_node": 3, "dst_node": 2, "bandwidth": 0.9, "latency": 25, "path_id": 1},
    # --- 路径 2 (主要服务于 节点 2, 3, 4 之间的连接) ---
    {"link_id": 4, "src_node": 2, "dst_node": 4, "bandwidth": 0.5, "latency": 80, "path_id": 2},
    {"link_id": 5, "src_node": 3, "dst_node": 4, "bandwidth": 0.6, "latency": 50, "path_id": 2},
    # --- 路径 3 (跨度最大的路径 0 -> 3 -> 4) ---
    {"link_id": 6, "src_node": 0, "dst_node": 3, "bandwidth": 1.0, "latency": 15, "path_id": 3},
    {"link_id": 7, "src_node": 3, "dst_node": 4, "bandwidth": 0.6, "latency": 50, "path_id": 3},
]

# 自定义VNR（虚拟网络请求）：VNF链+资源需求（与业务感知的QoS联动）
CUSTOM_VNR = {
    "vnf_chain": [0, 1],  # 2个VNF组成的链，固定为2个
    "vnf_cpu_demand": [0.2, 0.3],  # VNF0需0.2CPU，VNF1需0.3CPU（归一化）
    "vnf_mem_demand": [0.15, 0.2],  # VNF0/1的内存需求
    "link_bw_demand": [0.2]  # VNF0-VNF1之间的虚拟链路带宽需求
}