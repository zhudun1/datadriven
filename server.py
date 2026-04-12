#!/usr/bin/env python3
"""
统一的 HTTP 后端服务器
集成：用户注册登录 + 业务感知 + 智能编排 + 资源管理
前端所有请求统一入口：localhost:8003
"""
import json
import os
import sys
import threading
import traceback
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pymysql
import pymysql.cursors

PORT = 8003

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# 数据库配置
_MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
_MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
_MYSQL_USER = os.environ.get("MYSQL_USER", "root")
_MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "QosRoot@123")

DB_CONFIGS = {
    "user_center": {"host": _MYSQL_HOST, "port": _MYSQL_PORT, "user": _MYSQL_USER, "password": _MYSQL_PASSWORD, "database": "qos_user_center", "charset": "utf8mb4", "cursorclass": pymysql.cursors.DictCursor},
    "business_awareness": {"host": _MYSQL_HOST, "port": _MYSQL_PORT, "user": _MYSQL_USER, "password": _MYSQL_PASSWORD, "database": "business_awareness", "charset": "utf8mb4"},
    "intelligent_orchestration": {"host": _MYSQL_HOST, "port": _MYSQL_PORT, "user": _MYSQL_USER, "password": _MYSQL_PASSWORD, "database": "intelligent_orchestration", "charset": "utf8mb4", "cursorclass": pymysql.cursors.DictCursor},
}


def _db(key):
    return pymysql.connect(**DB_CONFIGS[key])


# ========== 全局模型（延迟加载）==========
_detector = None
_translator = None
_ppo_agent = None
_vne_env = None
_model_loaded = False
_model_loading_error = None

CLASS_NAME = "cable_gland"
CHECKPOINT_PATH = os.path.join(BASE_DIR, "checkpoints", "checkpoints_CFM_mvtec")
DEVICE = "cuda"  # will fallback to cpu

# ========== 消息队列 ==========
_mq_instance = None


def get_mq():
    global _mq_instance
    if _mq_instance is None:
        from common.message_queue import MessageQueue
        _mq_instance = MessageQueue()
    return _mq_instance


# ========== 认证 ==========

def do_login(username: str, password: str) -> dict:
    with _db("user_center") as conn:
        with conn.cursor() as c:
            c.execute("SELECT user_id, username, role FROM t_user WHERE username=%s AND password_hash=%s", (username, password))
            row = c.fetchone()
    if not row:
        return {"result": "fail", "message": "用户名或密码错误"}
    return {"result": "ok", "user_id": row["user_id"], "username": row["username"], "role": row["role"], "token": f"token-{row['user_id']}-{row['role']}"}


def do_register(username: str, password_hash: str) -> dict:
    try:
        with _db("user_center") as conn:
            with conn.cursor() as c:
                c.execute("INSERT INTO t_user (username, password_hash, role) VALUES (%s, %s, %s)", (username, password_hash, "user"))
                conn.commit()
        return {"result": "ok"}
    except Exception as e:
        code = e.args[0] if e.args else 0
        if code == 1062:
            return {"result": "fail", "message": "用户名已存在"}
        raise


# ========== 模型加载 ==========

def load_models():
    global _detector, _translator, _ppo_agent, _vne_env, _model_loaded, _model_loading_error
    if _model_loaded:
        return

    try:
        import torch

        global DEVICE
        DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

        from business_perception.cfm_detector import CFMDetector
        from business_perception.qos_translator import QoSTranslator
        from intelligent_orchestration.ppo_agent import GraphPPOOrchestrator
        from intelligent_orchestration.vne_env import GraphVNEEnv

        _detector = CFMDetector(CLASS_NAME, CHECKPOINT_PATH, DEVICE)
        _translator = QoSTranslator()
        _vne_env = GraphVNEEnv()
        _ppo_agent = GraphPPOOrchestrator(_vne_env)

        ppo_model_path = os.path.join(BASE_DIR, "ppo_graph_model.zip")
        if not os.path.exists(ppo_model_path):
            print("[Backend] PPO模型不存在，进行预热训练...")
            _ppo_agent.train_warmup(timesteps=200000)  # 使用与main.py相同的训练步数
            print("[Backend] PPO模型训练完成")
        else:
            print("[Backend] PPO模型已存在，跳过训练")

        _model_loaded = True
        print("[Backend] 所有模型加载完成")

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[Backend] 模型加载失败: {e}\n{tb}")
        _model_loading_error = str(e)


# ========== 图像加载 ==========

def load_rgb_pc(rgb_path: str, pcd_path: str):
    import torch
    from PIL import Image
    import numpy as np
    from torchvision import transforms
    sys.path.insert(0, BASE_DIR)
    from utils.mvtec3d_utils import read_tiff_organized_pc, organized_pc_to_depth_map, resize_organized_pc
    from utils.general_utils import SquarePad

    IMAGENET_MEAN = [0.485, 0.456, 0.406]
    IMAGENET_STD = [0.229, 0.224, 0.225]
    RGB_SIZE = 224

    img = Image.open(rgb_path).convert("RGB")
    img = transforms.Compose([
        SquarePad(),
        transforms.Resize((RGB_SIZE, RGB_SIZE), interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.ToTensor(),
        transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
    ])(img)

    organized_pc = read_tiff_organized_pc(pcd_path)
    depth_map_3ch = np.repeat(organized_pc_to_depth_map(organized_pc)[:, :, np.newaxis], 3, axis=2)
    resized_depth_map = resize_organized_pc(depth_map_3ch)
    resized_organized_pc = resize_organized_pc(organized_pc, target_height=RGB_SIZE, target_width=RGB_SIZE)

    return img.unsqueeze(0), resized_organized_pc.unsqueeze(0), resized_depth_map.unsqueeze(0)


# ========== 编排流水线 ==========

def run_pipeline(rgb_path: str, pcd_path: str, resource_req: dict):
    global _detector, _translator, _ppo_agent, _vne_env, _model_loaded

    if not _model_loaded:
        return {"status": "model_not_loaded", "error": _model_loading_error or "模型未加载"}

    try:
        # 1. 存储工业数据
        with _db("business_awareness") as conn:
            with conn.cursor() as c:
                c.execute("INSERT INTO t_industrial_data (rgb_path, pcd_path, create_time) VALUES (%s, %s, %s)", (rgb_path, pcd_path, datetime.now()))
                conn.commit()
                data_id = c.lastrowid

        # 2. 业务感知：异常检测 + QoS翻译
        rgb_t, pc_t, depth_t = load_rgb_pc(rgb_path, pcd_path)
        anomaly_score = _detector.get_anomaly_score(rgb_t, pc_t)
        risk_level, qos_vector = _translator.translate(float(anomaly_score))

        # 3. 智能编排：PPO决策
        obs, _ = _vne_env.reset()
        _vne_env.set_qos_to_vnr(qos_vector)
        obs = _vne_env._get_obs()
        action_dict = _ppo_agent.predict(obs)
        obs, reward, terminated, truncated, info = _vne_env.step(action_dict)

        # 4. 记录编排日志
        decision_plan = {
            "vnf_node": [int(n) for n in action_dict["vnf_node"]],
            "qos_vector": [round(float(x), 4) for x in qos_vector],
        }
        with _db("intelligent_orchestration") as conn:
            with conn.cursor() as c:
                c.execute("INSERT INTO t_orchestration_log (data_id, risk_snapshot, decision_plan, expected_reward, create_time) VALUES (%s, %s, %s, %s, %s)",
                    (data_id, json.dumps({"anomaly_score": round(float(anomaly_score), 4), "risk_level": risk_level}, ensure_ascii=False),
                     json.dumps(decision_plan, ensure_ascii=False), float(reward), datetime.now()))
                conn.commit()
                log_id = c.lastrowid

        # 5. 更新工业数据为已处理
        with _db("business_awareness") as conn:
            with conn.cursor() as c:
                c.execute("UPDATE t_industrial_data SET is_processed=1 WHERE data_id=%s", (data_id,))
                conn.commit()

        return {
            "status": "success",
            "data_id": data_id,
            "log_id": log_id,
            "anomaly_score": round(float(anomaly_score), 4),
            "risk_level": risk_level,
            "qos_vector": [round(float(x), 4) for x in qos_vector],
            "vnf_node": [int(n) for n in action_dict["vnf_node"]],
            "link_path": [],  # 链路通过BFS自动计算，不再输出
            "reward": round(float(reward), 2),
            "qos_ok": bool(info.get("qos_ok")),
            "resource_ok": bool(info.get("resource_ok")),
            "resource_plan": resource_req,
        }

    except Exception as e:
        tb = traceback.format_exc()
        return {"status": "error", "error": str(e), "trace": tb}


# ========== 资源管理 ==========

def get_active_resources():
    with _db("intelligent_orchestration") as conn:
        with conn.cursor() as c:
            c.execute("SELECT * FROM t_resource_inventory WHERE is_active=1")
            return c.fetchall()


def add_node(cpu: float, memory: float, energy_consumption: float, vcpu: int, memory_gb: int, storage: int, bandwidth: int) -> dict:
    """添加物理节点资源"""
    try:
        with _db("intelligent_orchestration") as conn:
            with conn.cursor() as c:
                # 获取最大ID
                c.execute("SELECT resource_id FROM t_resource_inventory WHERE resource_type='compute' ORDER BY resource_id DESC LIMIT 1")
                row = c.fetchone()
                new_id = int(row['resource_id'].split('-')[1]) + 1 if row else 0

                resource_id = f"node-{new_id}"
                current_state = {
                    "cpu": cpu,
                    "memory": memory,
                    "energy_consumption": energy_consumption,
                    "vcpu": vcpu,
                    "memory_gb": memory_gb,
                    "storage": storage,
                    "bandwidth": bandwidth
                }

                c.execute("INSERT INTO t_resource_inventory (resource_id, resource_name, resource_type, is_active, current_state) VALUES (%s, %s, %s, %s, %s)",
                    (resource_id, f"计算节点-{new_id}", "compute", 1, json.dumps(current_state)))
                conn.commit()
                return {"status": "success", "resource_id": resource_id, "message": f"节点 {resource_id} 添加成功"}
    except Exception as e:
        return {"status": "failed", "message": str(e)}


def add_link(src_node: int, dst_node: int, bandwidth: float, latency: float, path_id: int) -> dict:
    """添加物理链路资源"""
    try:
        with _db("intelligent_orchestration") as conn:
            with conn.cursor() as c:
                c.execute("SELECT resource_id FROM t_resource_inventory WHERE resource_type='network' ORDER BY resource_id DESC LIMIT 1")
                row = c.fetchone()
                new_id = int(row['resource_id'].split('-')[1]) + 1 if row else 0

                resource_id = f"link-{new_id}"
                current_state = {
                    "bandwidth": bandwidth,
                    "latency": latency,
                    "path_id": path_id,
                    "src": src_node,
                    "dst": dst_node
                }

                c.execute("INSERT INTO t_resource_inventory (resource_id, resource_name, resource_type, is_active, current_state) VALUES (%s, %s, %s, %s, %s)",
                    (resource_id, f"链路-{new_id} (节点{src_node}-节点{dst_node})", "network", 1, json.dumps(current_state)))
                conn.commit()
                return {"status": "success", "resource_id": resource_id, "message": f"链路 {resource_id} 添加成功"}
    except Exception as e:
        return {"status": "failed", "message": str(e)}


def delete_resource(resource_id: str) -> dict:
    """删除物理资源"""
    try:
        with _db("intelligent_orchestration") as conn:
            with conn.cursor() as c:
                c.execute("UPDATE t_resource_inventory SET is_active=0 WHERE resource_id=%s", (resource_id,))
                conn.commit()
                return {"status": "success", "resource_id": resource_id, "message": f"资源 {resource_id} 已删除"}
    except Exception as e:
        return {"status": "failed", "message": str(e)}


# ========== HTTP Handler ==========

class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, data, status=200):
        def json_safe(obj):
            import datetime as dt
            if isinstance(obj, (dt.datetime, dt.date, dt.time)):
                return obj.isoformat()
            if hasattr(obj, 'item'):
                return obj.item()
            if isinstance(obj, (int, float, bool, type(None))):
                return obj
            if isinstance(obj, str):
                return obj
            if isinstance(obj, dict):
                return {k: json_safe(v) for k, v in obj.items()}
            if hasattr(obj, '__iter__'):
                return [json_safe(x) for x in obj]
            return str(obj)

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(json_safe(data), ensure_ascii=False).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "3600")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/health":
            self._send_json({"status": "ok"})
            return

        # 资源查询
        if path == "/resources/active":
            auth = self.headers.get("Authorization", "")
            if not auth or not auth.startswith("Bearer "):
                self._send_json({"message": "未登录"}, 401)
                return

            try:
                resources = get_active_resources()
                self._send_json({"resources": resources})
            except Exception as e:
                self._send_json({"message": str(e)}, 500)
            return

        # 静态文件
        super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            data = {}

        path = self.path.split("?")[0]

        # ===== 登录 =====
        if path == "/login":
            result = do_login(data.get("username", ""), data.get("password", ""))
            self._send_json(result, 200 if result.get("result") == "ok" else 400)
            return

        # ===== 注册 =====
        if path == "/register":
            result = do_register(data.get("username", ""), data.get("password", ""))
            self._send_json(result, 200 if result.get("result") == "ok" else 400)
            return

        # ===== 添加节点 =====
        if path == "/resources/nodes":
            auth = self.headers.get("Authorization", "")
            if not auth or not auth.startswith("Bearer "):
                self._send_json({"message": "未登录"}, 401)
                return

            result = add_node(
                cpu=data.get("cpu", 0.5),
                memory=data.get("memory", 0.5),
                energy_consumption=data.get("energy_consumption", 0.5),
                vcpu=data.get("vcpu", 16),
                memory_gb=data.get("memory_gb", 32),
                storage=data.get("storage", 500),
                bandwidth=data.get("bandwidth", 10000)
            )
            if result.get("status") != "success":
                self._send_json(result, 500)
            else:
                self._send_json(result)
            return

        # ===== 添加链路 =====
        if path == "/resources/links":
            auth = self.headers.get("Authorization", "")
            if not auth or not auth.startswith("Bearer "):
                self._send_json({"message": "未登录"}, 401)
                return

            result = add_link(
                src_node=data.get("src_node", 0),
                dst_node=data.get("dst_node", 1),
                bandwidth=data.get("bandwidth", 1.0),
                latency=data.get("latency", 10),
                path_id=data.get("path_id", 0)
            )
            if result.get("status") != "success":
                self._send_json(result, 500)
            else:
                self._send_json(result)
            return

        # ===== 编排流水线 =====
        if path == "/pipeline":
            auth = self.headers.get("Authorization", "")
            if not auth or not auth.startswith("Bearer "):
                self._send_json({"message": "未登录，请先登录"}, 401)
                return

            rgb_path = data.get("businessImagePath", "")
            pcd_path = data.get("pointCloudPath", "")
            resource_req = data.get("resourceRequest", {})

            if not rgb_path or not pcd_path:
                self._send_json({"message": "请提供 RGB 图像路径和点云文件路径"}, 400)
                return

            resource_plan = {
                "vcpu": resource_req.get("vcpu", 8) if isinstance(resource_req, dict) else 8,
                "memory": resource_req.get("memory", 16) if isinstance(resource_req, dict) else 16,
                "storage": resource_req.get("storage", 100) if isinstance(resource_req, dict) else 100,
                "bandwidth": resource_req.get("bandwidth", 1000) if isinstance(resource_req, dict) else 1000,
                "latency": resource_req.get("latency", 5) if isinstance(resource_req, dict) else 5,
            }

            result = run_pipeline(rgb_path, pcd_path, resource_plan)
            status = 200 if result.get("status") == "success" else 500
            self._send_json(result, status)
            return

        self._send_json({"error": "unknown endpoint"}, 404)

    def do_DELETE(self):
        path = self.path.split("?")[0]

        # 删除资源 DELETE /resources/{resource_id}
        if path.startswith("/resources/"):
            auth = self.headers.get("Authorization", "")
            if not auth or not auth.startswith("Bearer "):
                self._send_json({"message": "未登录"}, 401)
                return

            resource_id = path.replace("/resources/", "")
            if not resource_id:
                self._send_json({"message": "无效的资源ID"}, 400)
                return

            result = delete_resource(resource_id)
            if result.get("status") != "success":
                self._send_json(result, 500)
            else:
                self._send_json(result)
            return

        self._send_json({"error": "unknown endpoint"}, 404)


# ========== 启动 ==========

if __name__ == "__main__":
    print("=" * 50)
    print("启动统一后端服务器: http://localhost:8003")
    print("包含: 登录注册 + 资源管理 + 编排流水线")
    print("加载深度学习模型中（首次启动有延迟）...")
    load_models()
    print("=" * 50)

    # 切换工作目录到 frontend 以便提供静态文件
    fe_dir = os.path.join(BASE_DIR, "frontend")
    if os.path.exists(fe_dir):
        os.chdir(fe_dir)

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print("后端服务运行于 http://localhost:8003")
    server.serve_forever()