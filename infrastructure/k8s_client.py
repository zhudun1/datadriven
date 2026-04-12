from kubernetes import client, config
from common.utils import init_logger
import json

logger = init_logger()

class K8sClient:
    def __init__(self, in_cluster: bool = False):
        """初始化K8s客户端（原型级：本地调试用kubeconfig，集群内用in_cluster）"""
        try:
            if in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config()  # 本地调试
            self.core_api = client.CoreV1Api()
            self.apps_api = client.AppsV1Api()
            self.network_api = client.NetworkingV1Api()
            logger.info("K8s客户端初始化成功")
        except Exception as e:
            #logger.error(f"K8s客户端初始化失败：{e}，使用模拟客户端")
            self._mock_mode = True
        else:
            self._mock_mode = False

    def deploy_vnf(self, vnf_name: str, node_id: str, cpu_request: str, mem_request: str):
        """部署VNF到指定节点（Pod）"""
        if self._mock_mode:
            #logger.info(f"[模拟] 部署VNF {vnf_name} 到节点 {node_id}，CPU：{cpu_request}，内存：{mem_request}")
            return {"status": "success", "pod_name": f"{vnf_name}-pod-001"}
        # 真实K8s API调用
        pod_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": vnf_name},
            "spec": {
                "nodeSelector": {"kubernetes.io/hostname": node_id},
                "containers": [{
                    "name": vnf_name,
                    "image": "your-vnf-image:latest",
                    "resources": {
                        "requests": {"cpu": cpu_request, "memory": mem_request},
                        "limits": {"cpu": cpu_request, "memory": mem_request}
                    }
                }]
            }
        }
        try:
            resp = self.core_api.create_namespaced_pod(
                body=pod_manifest,
                namespace="default"
            )
            logger.info(f"部署VNF {vnf_name} 成功，Pod名称：{resp.metadata.name}")
            return {"status": "success", "pod_name": resp.metadata.name}
        except Exception as e:
            logger.error(f"部署VNF失败：{e}")
            return {"status": "failed", "error": str(e)}

    def delete_vnf(self, vnf_name: str):
        """删除VNF Pod"""
        if self._mock_mode:
            logger.info(f"[模拟] 删除VNF {vnf_name}")
            return {"status": "success"}
        try:
            self.core_api.delete_namespaced_pod(
                name=vnf_name,
                namespace="default"
            )
            return {"status": "success"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    def get_node_resources(self, node_id: str):
        """获取节点资源使用情况"""
        if self._mock_mode:
            return {
                "cpu_usage": "20%",
                "mem_usage": "30%",
                "available_cpu": "800m",
                "available_mem": "8Gi"
            }
        try:
            node = self.core_api.read_node(node_id)
            # 解析节点资源
            return {
                "cpu_usage": node.status.allocatable["cpu"],
                "mem_usage": node.status.allocatable["memory"],
                "available_cpu": node.status.capacity["cpu"],
                "available_mem": node.status.capacity["memory"]
            }
        except Exception as e:
            logger.error(f"获取节点资源失败：{e}")
            return {"status": "failed", "error": str(e)}