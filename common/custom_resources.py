# common/custom_resources.py
# 自定义物理节点/链路资源（新版多维属性模型 - 备用/调试用）
# 注：实际生产环境使用数据库加载资源

CUSTOM_NODES = [
    {"node_id": 0, "node_name": "边缘计算节点1", "domain": "edge", "role": "compute",
     "cpu": 1.0, "memory": 1.0, "energy_consumption": 0.225,
     "total_cpu": 8, "total_memory": 32, "used_cpu_cores": 0.0, "used_memory_gb": 0.0},
    {"node_id": 1, "node_name": "边缘计算节点2", "domain": "edge", "role": "compute",
     "cpu": 1.0, "memory": 1.0, "energy_consumption": 0.225,
     "total_cpu": 8, "total_memory": 32, "used_cpu_cores": 0.0, "used_memory_gb": 0.0},
    {"node_id": 2, "node_name": "边缘计算节点3", "domain": "edge", "role": "compute",
     "cpu": 1.0, "memory": 1.0, "energy_consumption": 0.15,
     "total_cpu": 4, "total_memory": 16, "used_cpu_cores": 0.0, "used_memory_gb": 0.0},
    {"node_id": 3, "node_name": "云端GPU服务器1", "domain": "cloud", "role": "compute",
     "cpu": 1.0, "memory": 1.0, "energy_consumption": 1.0,
     "total_cpu": 16, "total_memory": 128, "used_cpu_cores": 0.0, "used_memory_gb": 0.0},
    {"node_id": 4, "node_name": "云端GPU服务器2", "domain": "cloud", "role": "compute",
     "cpu": 1.0, "memory": 1.0, "energy_consumption": 1.75,
     "total_cpu": 32, "total_memory": 256, "used_cpu_cores": 0.0, "used_memory_gb": 0.0},
]

CUSTOM_LINKS = [
    {"link_id": 0, "link_id_str": "link-edge-1-2", "src_node": 0, "dst_node": 1, "bandwidth": 1.0, "latency": 0.02},
    {"link_id": 1, "link_id_str": "link-edge-2-3", "src_node": 1, "dst_node": 2, "bandwidth": 1.0, "latency": 0.03},
    {"link_id": 2, "link_id_str": "link-edge-1-3", "src_node": 0, "dst_node": 2, "bandwidth": 0.5, "latency": 0.05},
    {"link_id": 3, "link_id_str": "link-edge-1-cloud1", "src_node": 0, "dst_node": 3, "bandwidth": 1.0, "latency": 0.15},
    {"link_id": 4, "link_id_str": "link-edge-2-cloud1", "src_node": 1, "dst_node": 3, "bandwidth": 1.0, "latency": 0.15},
    {"link_id": 5, "link_id_str": "link-edge-3-cloud2", "src_node": 2, "dst_node": 4, "bandwidth": 1.0, "latency": 0.20},
    {"link_id": 6, "link_id_str": "link-cloud1-2", "src_node": 3, "dst_node": 4, "bandwidth": 1.0, "latency": 0.05},
    {"link_id": 7, "link_id_str": "link-edge-1-cloud2", "src_node": 0, "dst_node": 4, "bandwidth": 1.0, "latency": 0.18},
]

CUSTOM_VNR = {
    "vnf_chain": [0, 1],
    "vnf_cpu_demand": [0.2, 0.3],
    "vnf_mem_demand": [0.15, 0.2],
    "link_bw_demand": [0.2]
}