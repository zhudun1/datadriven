import requests
from common.utils import init_logger
import json

logger = init_logger()

class SDNController:
    def __init__(self, controller_url: str = "http://127.0.0.1:8181"):
        """初始化SDN控制器（支持ONOS/ODL，原型级用模拟）"""
        self.controller_url = controller_url
        self.headers = {"Content-Type": "application/json"}
        # 测试连接
        try:
            resp = requests.get(f"{self.controller_url}/v1/topology", timeout=5)
            if 200 <= resp.status_code < 300:
                self._mock_mode = False
                logger.info(f"SDN控制器连接成功，状态码：{resp.status_code}")
            else:
                self._mock_mode = True
                logger.warning(
                    f"SDN控制器探测失败（HTTP {resp.status_code}），切换模拟模式: {self.controller_url}"
                )
        except Exception as exc:
            self._mock_mode = True
            logger.warning(f"SDN控制器不可达，切换模拟模式: {exc}")

    def deploy_flow_rule(self, path_id: int, bandwidth: float, latency: float, priority: int):
        """下发流表规则（带宽/时延/优先级）"""
        if self._mock_mode:
            logger.info(
                f"[模拟] 下发流表：路径ID {path_id}，带宽 {bandwidth}Mbps，时延 {latency}ms，优先级 {priority}"
            )
            return {"status": "success", "flow_id": f"flow-{path_id}-{priority}"}
        # 真实SDN控制器API调用（ONOS示例）
        flow_rule = {
            "priority": priority,
            "timeout": 0,
            "is_permanent": True,
            "deviceId": f"of:000000000000000{path_id}",
            "treatment": {
                "instructions": [
                    {"type": "OUTPUT", "port": "NORMAL"}
                ]
            },
            "selector": {
                "criteria": [
                    {"type": "BW", "bandwidth": bandwidth},
                    {"type": "LATENCY", "latency": latency}
                ]
            }
        }
        try:
            url = f"{self.controller_url}/v1/flows"
            resp = requests.post(
                url,
                headers=self.headers,
                data=json.dumps(flow_rule),
                timeout=10
            )
            if resp.status_code in (200, 201, 202):
                flow_id = ""
                try:
                    payload = resp.json()
                    if isinstance(payload, dict):
                        flow_id = str(payload.get("flowId", "") or payload.get("id", ""))
                except Exception:
                    flow_id = ""
                return {
                    "status": "success",
                    "flow_id": flow_id or f"flow-{path_id}-{priority}",
                    "http_status": resp.status_code,
                }
            return {
                "status": "failed",
                "http_status": resp.status_code,
                "url": url,
                "error": (resp.text or "").strip(),
            }
        except Exception as e:
            logger.error(f"下发流表失败：{e}")
            return {"status": "failed", "url": f"{self.controller_url}/v1/flows", "error": str(e)}

    def delete_flow_rule(self, flow_id: str):
        """删除流表规则"""
        if self._mock_mode:
            logger.info(f"[模拟] 删除流表 {flow_id}")
            return {"status": "success"}
        try:
            resp = requests.delete(
                f"{self.controller_url}/v1/flows/{flow_id}",
                headers=self.headers,
                timeout=5
            )
            return {"status": "success" if resp.status_code == 204 else "failed"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    def get_link_status(self, link_id: int):
        """获取链路状态（带宽/时延/丢包率）"""
        if self._mock_mode:
            return {
                "bandwidth": "100Mbps",
                "latency": "10ms",
                "loss_rate": "0.1%"
            }
        try:
            resp = requests.get(
                f"{self.controller_url}/v1/links/{link_id}",
                headers=self.headers,
                timeout=5
            )
            return resp.json()
        except Exception as e:
            logger.error(f"获取链路状态失败：{e}")
            return {"status": "failed", "error": str(e)}
