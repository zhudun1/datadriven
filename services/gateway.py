"""
API网关微服务
接收前端编排请求，写数据库，发队列，轮询等待结果返回前端
"""
import sys
import os
import json
import uuid

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 环境变量支持 Docker 部署
MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "QosRoot@123")

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from common.message_queue import MessageQueue, get_message_queue
from common.utils import init_logger

logger = init_logger()

app = FastAPI(title="数据驱动编排系统 API", version="1.0.0")

# 全局感知服务实例，避免重复加载模型
_perception_service = {}
_orchestration_service = {}

# CORS 配置 - 允许所有来源，因为前端和API在不同端口
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 数据模型 ==========

class OrchRequest(BaseModel):
    model_type: str = "CFM"  # 或 "Jigsaw"
    # CFM模式
    businessImagePath: Optional[str] = None
    pointCloudPath: Optional[str] = None
    # Jigsaw模式
    videoPath: Optional[str] = None
    auxiliaryImagePath: Optional[str] = None
    # 通用
    resourceRequest: Optional[dict] = {}


class AddNodeRequest(BaseModel):
    node_id: Optional[int] = None
    cpu: float = 0.5
    memory: float = 0.5
    energy_consumption: float = 0.5
    vcpu: int = 16
    memory_gb: int = 32
    storage: int = 500
    bandwidth: int = 10000


class AddLinkRequest(BaseModel):
    link_id: Optional[int] = None
    src_node: int
    dst_node: int
    bandwidth: float = 1.0
    latency: float = 10.0
    path_id: int = 0


import pymysql
import re
from datetime import datetime


# ========== 辅助函数：生成新ID ==========

def _gen_next_node_id() -> str:
    """生成下一个 node_id 如 'edge-3'"""
    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT,
        user=MYSQL_USER, password=MYSQL_PASSWORD,
        database='intelligent_orchestration', charset='utf8mb4'
    )
    try:
        with conn.cursor() as c:
            c.execute("SELECT node_id FROM t_physical_node ORDER BY node_id DESC LIMIT 1")
            row = c.fetchone()
            if row:
                m = re.search(r'(\d+)$', row[0])
                next_num = int(m.group(1)) + 1 if m else 1
                prefix = row[0][:len(row[0]) - len(str(m.group(1)))]
                return f"{prefix}{next_num}"
            return "edge-1"
    finally:
        conn.close()


def _gen_next_link_id(src_node: str, dst_node: str) -> str:
    return f"link-{src_node}-{dst_node}"


def _gen_link_id_from_nodes(src_node: str, dst_node: str) -> str:
    """从源节点和目标节点生成link_id"""
    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT,
        user=MYSQL_USER, password=MYSQL_PASSWORD,
        database='intelligent_orchestration', charset='utf8mb4'
    )
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT link_id FROM t_physical_link WHERE src_node=%s AND dst_node=%s LIMIT 1",
                (src_node, dst_node)
            )
            row = c.fetchone()
            if row:
                return row[0]
            # 不存在则生成新的
            c.execute("SELECT link_id FROM t_physical_link ORDER BY link_id DESC LIMIT 1")
            row = c.fetchone()
            if row:
                m = re.search(r'link-.*?-(\d+)$', row[0])
                next_num = int(m.group(1)) + 1 if m else 1
                return f"link-{src_node}-{dst_node}-{next_num}"
            return f"link-{src_node}-{dst_node}-1"
    finally:
        conn.close()


# ========== 数据库写入工具 ==========

def write_industrial_data(req: OrchRequest) -> int:
    import pymysql
    from datetime import datetime

    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": "business_awareness", "charset": "utf8mb4"
    }
    conn = pymysql.connect(**conn_cfg)
    with conn.cursor() as c:
        c.execute(
            "INSERT INTO t_industrial_data (rgb_path, pcd_path, create_time) "
            "VALUES (%s, %s, %s)",
            (
                req.businessImagePath or req.videoPath or "",
                req.pointCloudPath or "",
                datetime.now()
            )
        )
        conn.commit()
        data_id = int(c.lastrowid)
    conn.close()
    return data_id


# ========== API端点 ==========

@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/pipeline")
def submit_orchestration(
    req: OrchRequest,
    authorization: Optional[str] = Header(None)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    try:
        mq = get_message_queue()

        # 验证请求参数
        if req.model_type == "CFM":
            if not req.businessImagePath or not req.pointCloudPath:
                raise HTTPException(status_code=400, detail="CFM模式需要图片和点云路径")
        elif req.model_type == "Jigsaw":
            if not req.videoPath and not req.auxiliaryImagePath:
                raise HTTPException(status_code=400, detail="Jigsaw模式需要视频或图像路径")

        data_id = write_industrial_data(req)
        logger.info(f"工业数据写入成功: data_id={data_id}")

        # 构建消息
        message = {
            "task_id": str(data_id),
            "data_id": data_id,
            "model_type": req.model_type,
            "resource_request": req.resourceRequest or {}
        }

        if req.model_type == "CFM":
            message["rgb_path"] = req.businessImagePath
            message["pcd_path"] = req.pointCloudPath
        else:  # Jigsaw
            message["video_path"] = req.videoPath or ""
            message["aux_image_path"] = req.auxiliaryImagePath or ""

        # 直接调用感知服务，避免消息队列问题
        try:
            from services.perception_service import PerceptionService
            perception = PerceptionService()
            result = perception.process(message)
            logger.info(f"直接调用感知服务: task_id={message['task_id']}, anomaly={result.get('anomaly_score', 0.5)}")
        except Exception as e:
            logger.error(f"感知服务调用失败: {e}")
            # 降级到队列方式
            mq.publish(MessageQueue.QUEUE_PERCEPTION, message)
            logger.info(f"任务已提交到感知队列: task_id={message['task_id']}, model_type={message.get('model_type')}")
            result = mq.get_result(message["task_id"], timeout=120)

        if result is None:
            return {"status": "timeout", "message": "处理超时"}

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"提交任务失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/resources/active")
def list_resources(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    import pymysql
    import datetime as dt_module

    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": "intelligent_orchestration", "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }
    conn = pymysql.connect(**conn_cfg)

    # 查询活跃节点和链路
    resources = []

    with conn.cursor() as c:
        # 查询 t_physical_node JOIN t_node_compute/t_node_storage
        c.execute("""
            SELECT n.node_id, n.node_name, n.domain, n.role, n.is_active,
                   c.cpu_cores, c.used_cpu_cores,
                   s.ram_gb, s.used_ram_gb
            FROM t_physical_node n
            LEFT JOIN t_node_compute c ON n.node_id = c.node_id
            LEFT JOIN t_node_storage s ON n.node_id = s.node_id
            WHERE n.is_active = 1
        """)
        nodes = c.fetchall()

        for row in nodes:
            resources.append({
                "resource_id": row["node_id"],
                "resource_name": row["node_name"],
                "resource_type": "compute",
                "domain": row["domain"],
                "role": row["role"],
                "is_active": row["is_active"],
                "current_state": {
                    "cpu_cores": row.get("cpu_cores", 0),
                    "used_cpu_cores": row.get("used_cpu_cores", 0),
                    "free_cpu_cores": max(0, row.get("cpu_cores", 0) - row.get("used_cpu_cores", 0)),
                    "ram_gb": row.get("ram_gb", 0),
                    "used_ram_gb": row.get("used_ram_gb", 0),
                    "free_ram_gb": max(0, row.get("ram_gb", 0) - row.get("used_ram_gb", 0))
                }
            })

        # 查询物理链路
        c.execute("""
            SELECT link_id, link_name, src_node, dst_node,
                   bandwidth_mbps, used_bandwidth_mbps,
                   propagation_delay_ms, queue_policy
            FROM t_physical_link
            WHERE is_active = 1
        """)
        links = c.fetchall()

        for row in links:
            resources.append({
                "resource_id": row["link_id"],
                "resource_name": row["link_name"],
                "resource_type": "network",
                "is_active": 1,
                "current_state": {
                    "src_node": row["src_node"],
                    "dst_node": row["dst_node"],
                    "bandwidth_mbps": row["bandwidth_mbps"],
                    "used_bandwidth_mbps": row["used_bandwidth_mbps"],
                    "free_bandwidth_mbps": max(0, row["bandwidth_mbps"] - row["used_bandwidth_mbps"]),
                    "propagation_delay_ms": row["propagation_delay_ms"],
                    "queue_policy": row["queue_policy"]
                }
            })
    conn.close()

    def json_safe(obj):
        if isinstance(obj, (dt_module.datetime, dt_module.date, dt_module.time)):
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

    return {"resources": json_safe(resources)}


@app.get("/resources/status")
async def get_resource_status(authorization: Optional[str] = Header(None)):
    """获取资源详细状态（含预留和可用资源）"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    # 直接调用编排服务的资源状态函数
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from services.orchestration_service import get_resource_status

    try:
        resources = get_resource_status()
        return {
            "status": "success",
            "resources": resources,
            "total_count": len(resources)
        }
    except Exception as e:
        logger.error(f"获取资源状态失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/resources/check")
async def check_resources(authorization: Optional[str] = Header(None), request: Request = None):
    """预检查资源是否足够"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    try:
        body = await request.json()
    except Exception:
        body = {}

    qos_vector = body.get("qos_vector", [0.8, 0.1, 1.0, 0.05])

    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from services.orchestration_service import check_resource_availability

    try:
        result = check_resource_availability(qos_vector, wait_seconds=5)
        return {
            "status": "success",
            "check_result": result
        }
    except Exception as e:
        logger.error(f"资源检查失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def submit_resource_operation(operation_type: str, resource_data: dict = None, resource_id: str = None, custom_id: int = None) -> dict:
    """直接操作数据库（简化版，不走消息队列）"""
    import pymysql
    import json

    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": "intelligent_orchestration", "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }
    conn = pymysql.connect(**conn_cfg)

    try:
        with conn.cursor() as c:
            if operation_type == "add_node":
                # 使用自定义ID或自动生成
                if custom_id is not None:
                    node_id = f"edge-{custom_id}"
                    # 检查是否已存在
                    c.execute("SELECT node_id FROM t_physical_node WHERE node_id=%s", (node_id,))
                    if c.fetchone():
                        return {"status": "failed", "message": f"节点 {node_id} 已存在"}
                else:
                    # 自动生成下一个 node_id
                    node_id = _gen_next_node_id()

                cpu_cores = resource_data.get("vcpu", 16)
                ram_gb = resource_data.get("memory_gb", 32)

                # INSERT t_physical_node
                c.execute(
                    "INSERT INTO t_physical_node (node_id, node_name, domain, role, is_active) VALUES (%s, %s, %s, %s, %s)",
                    (node_id, f"计算节点-{node_id}", "edge", "compute", 1)
                )
                # INSERT t_node_compute
                c.execute(
                    "INSERT INTO t_node_compute (node_id, cpu_cores, used_cpu_cores) VALUES (%s, %s, %s)",
                    (node_id, cpu_cores, 0)
                )
                # INSERT t_node_storage
                c.execute(
                    "INSERT INTO t_node_storage (node_id, ram_gb, used_ram_gb) VALUES (%s, %s, %s)",
                    (node_id, ram_gb, 0)
                )
                conn.commit()
                return {"status": "success", "resource_id": node_id, "message": f"节点 {node_id} 添加成功"}

            elif operation_type == "add_link":
                # 使用自定义ID或自动生成
                if custom_id is not None:
                    link_id = f"link-{custom_id}"
                    # 检查是否已存在
                    c.execute("SELECT link_id FROM t_physical_link WHERE link_id=%s", (link_id,))
                    if c.fetchone():
                        return {"status": "failed", "message": f"链路 {link_id} 已存在"}
                else:
                    src = str(resource_data.get("src_node", "edge-1"))
                    dst = str(resource_data.get("dst_node", "edge-2"))
                    link_id = _gen_next_link_id(src, dst)

                src_node = str(resource_data.get("src_node", "edge-1"))
                dst_node = str(resource_data.get("dst_node", "edge-2"))
                bandwidth = resource_data.get("bandwidth", 1000)
                latency = resource_data.get("latency", 5)

                # INSERT t_physical_link
                c.execute(
                    "INSERT INTO t_physical_link (link_id, link_name, src_node, dst_node, bandwidth_mbps, propagation_delay_ms, queue_policy, is_active, used_bandwidth_mbps) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (link_id, f"链路-{link_id}", src_node, dst_node, bandwidth, latency, "FIFO", 1, 0)
                )
                conn.commit()
                return {"status": "success", "resource_id": link_id, "message": f"链路 {link_id} 添加成功"}

            elif operation_type == "delete_node" or operation_type == "delete_link":
                # 判断是节点还是链路
                if resource_id.startswith("edge-"):
                    c.execute("UPDATE t_physical_node SET is_active=0 WHERE node_id=%s", (resource_id,))
                elif resource_id.startswith("link-"):
                    c.execute("UPDATE t_physical_link SET is_active=0 WHERE link_id=%s", (resource_id,))
                elif resource_id.startswith("node-"):  # 兼容旧格式
                    c.execute("UPDATE t_physical_node SET is_active=0 WHERE node_id=%s", (resource_id.replace("node-", "edge-"),))
                conn.commit()
                return {"status": "success", "resource_id": resource_id, "message": f"资源 {resource_id} 已删除"}

            return {"status": "failed", "message": "未知操作类型"}

    except Exception as e:
        return {"status": "failed", "message": str(e)}
    finally:
        conn.close()


@app.post("/resources/nodes")
def add_node(req: AddNodeRequest, authorization: Optional[str] = Header(None)):
    """添加物理节点资源（通过消息队列）"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    resource_data = {
        "cpu": req.cpu,
        "memory": req.memory,
        "energy_consumption": req.energy_consumption,
        "vcpu": req.vcpu,
        "memory_gb": req.memory_gb,
        "storage": req.storage,
        "bandwidth": req.bandwidth
    }

    result = submit_resource_operation("add_node", resource_data=resource_data, custom_id=req.node_id)

    if result.get("status") != "success":
        raise HTTPException(status_code=500, detail=result.get("message", "添加节点失败"))

    return {"status": "success", "resource_id": result.get("resource_id"), "message": result.get("message")}


@app.post("/resources/links")
def add_link(req: AddLinkRequest, authorization: Optional[str] = Header(None)):
    """添加物理链路资源（通过消息队列）"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    resource_data = {
        "src_node": req.src_node,
        "dst_node": req.dst_node,
        "bandwidth": req.bandwidth,
        "latency": req.latency,
        "path_id": req.path_id
    }

    result = submit_resource_operation("add_link", resource_data=resource_data, custom_id=req.link_id)

    if result.get("status") != "success":
        raise HTTPException(status_code=500, detail=result.get("message", "添加链路失败"))

    return {"status": "success", "resource_id": result.get("resource_id"), "message": result.get("message")}


@app.delete("/resources/{resource_id}")
def delete_resource(resource_id: str, authorization: Optional[str] = Header(None)):
    """删除物理资源（通过消息队列）"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    # 识别资源类型：edge-* 为节点，link-* 为链路
    if resource_id.startswith("edge-"):
        operation_type = "delete_node"
    elif resource_id.startswith("link-"):
        operation_type = "delete_link"
    elif resource_id.startswith("node-"):  # 兼容旧格式
        operation_type = "delete_node"
    else:
        raise HTTPException(status_code=400, detail="无效的资源ID")

    result = submit_resource_operation(operation_type, resource_id=resource_id)

    if result.get("status") != "success":
        raise HTTPException(status_code=500, detail=result.get("message", "删除资源失败"))

    return {"status": "success", "resource_id": resource_id, "message": result.get("message")}


@app.get("/orchestration/history")
async def get_orchestration_history(
    authorization: Optional[str] = Header(None),
    limit: int = 20
):
    """获取编排历史记录"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    import pymysql
    import json

    MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
    MYSQL_USER = os.environ.get("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "QosRoot@123")

    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT,
        user=MYSQL_USER, password=MYSQL_PASSWORD,
        database="intelligent_orchestration", charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )

    try:
        with conn.cursor() as c:
            c.execute(
                f"SELECT * FROM t_orchestration_log ORDER BY create_time DESC LIMIT %s",
                (limit,)
            )
            rows = c.fetchall()

        history = []
        for row in rows:
            decision_plan = row.get("decision_plan", {})
            if isinstance(decision_plan, str):
                decision_plan = json.loads(decision_plan)
            history.append({
                "id": row.get("log_id"),
                "data_id": row.get("data_id"),
                "vnf_node": decision_plan.get("vnf_node", []),
                "qos_vector": decision_plan.get("qos_vector", []),
                "reward": row.get("expected_reward"),
                "qos_ok": True,  # 从日志中可解析
                "create_time": str(row.get("create_time", ""))
            })
        return {"status": "success", "history": history}
    finally:
        conn.close()


@app.get("/resources/waiting")
async def get_waiting_resources(authorization: Optional[str] = Header(None)):
    """获取5秒内即将释放的资源"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    import pymysql
    from datetime import datetime

    MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
    MYSQL_USER = os.environ.get("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "QosRoot@123")

    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT,
        user=MYSQL_USER, password=MYSQL_PASSWORD,
        database="intelligent_orchestration", charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )

    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT allocation_id, node_allocation, release_time
                FROM t_service_allocation
                WHERE status = 'active'
                  AND release_time IS NOT NULL
                  AND release_time <= DATE_ADD(NOW(), INTERVAL 5 SECOND)
                  AND release_time > NOW()
                ORDER BY release_time ASC
            """)
            rows = c.fetchall()

        waiting = []
        for row in rows:
            node_alloc = row.get("node_allocation", {})
            if isinstance(node_alloc, str):
                node_alloc = json.loads(node_alloc)

            # 计算释放的资源量
            releasing = {"cpu": 0, "memory": 0}
            for rid, usage in node_alloc.items():
                if isinstance(usage, dict):
                    releasing["cpu"] += usage.get("vcpu", 0)
                    releasing["memory"] += usage.get("memory_gb", 0)

            # 提取node_id
            node_ids = []
            for rid in node_alloc.keys():
                if rid.startswith("node-"):
                    node_ids.append(rid.replace("node-", ""))

            waiting.append({
                "allocation_id": row.get("allocation_id"),
                "node_ids": node_ids,
                "seconds_left": row.get("seconds_left", 0),
                "releasing_cpu": releasing["cpu"],
                "releasing_memory": releasing["memory"],
                "release_time": str(row.get("release_time", ""))
            })

        return {"status": "success", "waiting": waiting}
    finally:
        conn.close()


# ========== 资源管理 API ==========

@app.get("/api/resources")
def get_resources():
    """获取资源剩余量"""
    import pymysql

    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": "intelligent_orchestration",
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }
    conn = pymysql.connect(**conn_cfg)

    try:
        with conn.cursor() as c:
            # 从 t_physical_node JOIN t_node_compute/t_node_storage 读取
            c.execute("""
                SELECT n.node_id, n.node_name,
                       c.cpu_cores, c.used_cpu_cores,
                       s.ram_gb, s.used_ram_gb
                FROM t_physical_node n
                LEFT JOIN t_node_compute c ON n.node_id = c.node_id
                LEFT JOIN t_node_storage s ON n.node_id = s.node_id
                WHERE n.is_active = 1
            """)
            rows = c.fetchall()

            nodes = {}
            for row in rows:
                node_id = row["node_id"]

                cpu_cores = row.get("cpu_cores", 0)
                used_cpu = row.get("used_cpu_cores", 0)
                ram_gb = row.get("ram_gb", 0)
                used_ram = row.get("used_ram_gb", 0)

                nodes[node_id] = {
                    "resource_id": node_id,
                    "free_vcpu": max(0, cpu_cores - used_cpu),
                    "free_memory_gb": max(0, ram_gb - used_ram),
                    "total_vcpu": cpu_cores,
                    "total_memory_gb": ram_gb,
                    "used_vcpu": used_cpu,
                    "used_memory_gb": used_ram,
                }
            return nodes
    finally:
        conn.close()


@app.get("/api/links")
def get_links():
    """获取网络链路列表"""
    import pymysql

    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": "intelligent_orchestration",
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }
    conn = pymysql.connect(**conn_cfg)

    try:
        with conn.cursor() as c:
            # 从 t_physical_link 读取
            c.execute("""
                SELECT link_id, link_name, src_node, dst_node,
                       bandwidth_mbps, used_bandwidth_mbps,
                       propagation_delay_ms, queue_policy
                FROM t_physical_link
                WHERE is_active = 1
            """)
            rows = c.fetchall()

            links = {}
            for row in rows:
                link_id = row["link_id"]
                bw = row.get("bandwidth_mbps", 0)
                used_bw = row.get("used_bandwidth_mbps", 0) or 0

                links[link_id] = {
                    "link_id": link_id,
                    "link_name": row.get("link_name", ""),
                    "bandwidth_mbps": bw,
                    "used_bandwidth_mbps": used_bw,
                    "free_bw": max(0, bw - used_bw),
                    "latency_ms": row.get("propagation_delay_ms", 0),
                    "src_node": row.get("src_node"),
                    "dst_node": row.get("dst_node"),
                    "queue_policy": row.get("queue_policy"),
                }
            return links
    finally:
        conn.close()


@app.get("/api/thresholds")
def get_thresholds():
    """获取异常检测阈值配置"""
    import os
    threshold_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "threshold_config.json")
    try:
        with open(threshold_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"cfm": {"CRITICAL": 0.7899, "HIGH": 0.765, "MEDIUM": 0.7413}, "jigsaw": {"CRITICAL": 0.9763, "HIGH": 0.917, "MEDIUM": 0.8306}}


@app.get("/api/qos-rules")
def get_qos_rules():
    """获取QoS规则表（基于数据特征组合）"""
    from business_perception.qos_translator import QoSTranslator

    translator = QoSTranslator()

    return {
        "status": "success",
        "profiles": translator.get_profiles_table(),
        "description": "QoS由数据特征组合决定：数据大小 + 实时性要求"
    }


class OrchRequestSimple(BaseModel):
    task_id: str
    qos_vector: list = [0.8, 0.1, 1.0, 0.05]
    resource_request: dict = {"vcpu": 8, "memory": 16}
    vnf_count: int = 2  # VNF数量，可配置
    rgb_path: str = ""
    pcd_path: str = ""
    video_path: str = ""
    aux_image_path: str = ""
    model_type: str = "CFM"


class OrchCheckRequest(BaseModel):
    qos_vector: list = [0.8, 0.1, 1.0, 0.05]
    resource_request: dict = {"vcpu": 8, "memory": 16}
    vnf_count: int = 2
    model_type: str = "CFM"


@app.post("/api/orchestrate")
def orchestrate_simple(req: OrchRequestSimple):
    """简易编排接口（调用感知服务获取真实分数 + 编排服务）"""
    # 首先调用感知服务获取真实异常分数
    anomaly_score = 0.5
    risk_level = "NORMAL"
    qos_vector = req.qos_vector
    model_type = req.model_type or "CFM"

    # 根据模型类型判断是否有有效文件路径
    has_valid_files = False
    if model_type == "CFM":
        has_valid_files = req.rgb_path and req.pcd_path
    else:  # Jigsaw
        has_valid_files = req.video_path or req.aux_image_path

    if has_valid_files:
        try:
            # 使用全局感知服务实例，避免重复加载模型
            global _perception_service
            if not hasattr(_perception_service, 'perception'):
                from services.perception_service import PerceptionService
                _perception_service['perception'] = PerceptionService()
            perception = _perception_service['perception']
            perc_message = {
                "task_id": req.task_id,
                "data_id": 0,
                "model_type": model_type,
                "rgb_path": req.rgb_path,
                "pcd_path": req.pcd_path,
                "video_path": req.video_path,
                "aux_image_path": req.aux_image_path
            }
            perc_result = perception.process(perc_message)
            if perc_result and perc_result.get("status") == "success":
                anomaly_score = perc_result.get("anomaly_score", 0.5)
                risk_level = perc_result.get("risk_level", "NORMAL")
                qos_vector = perc_result.get("qos_vector", req.qos_vector)
                logger.info(f"感知服务返回: model={model_type}, anomaly={anomaly_score}, risk={risk_level}")
        except Exception as e:
            logger.error(f"感知服务调用失败: {e}，使用默认值")
    else:
        # 无文件路径时使用随机值模拟异常检测结果
        import random
        anomaly_score = round(random.uniform(0.4, 0.7), 4)
        if anomaly_score > 0.65:
            risk_level = "HIGH"
        elif anomaly_score > 0.55:
            risk_level = "MEDIUM"
        else:
            risk_level = "NORMAL"
        logger.info(f"模拟异常检测: anomaly={anomaly_score}, risk={risk_level}")

    # 调用编排服务
    from intelligent_orchestration.orchestration_service import OrchestrationService
    global _orchestration_service
    if 'service' not in _orchestration_service:
        _orchestration_service['service'] = OrchestrationService()
    service = _orchestration_service['service']

    # 编排前清理过期资源
    try:
        service.lease_manager.release_due_allocations()
    except Exception as e:
        logger.warning(f"auto cleanup warning: {e}")

    message = {
        "task_id": req.task_id,
        "data_id": 0,
        "qos_vector": qos_vector,
        "resource_request": req.resource_request,
        "vnf_count": req.vnf_count,  # VNF数量
        "anomaly_score": anomaly_score,
        "risk_level": risk_level,
    }

    result = service.process(message)
    return result


@app.post("/api/orchestrate/check")
def orchestrate_check(req: OrchCheckRequest):
    """预检查资源状态，返回等待/不足/充足"""
    from intelligent_orchestration.orchestration_service import OrchestrationService

    global _orchestration_service
    if 'service' not in _orchestration_service:
        _orchestration_service['service'] = OrchestrationService()
    service = _orchestration_service['service']

    result = service.check_resources(
        qos_vector=req.qos_vector,
        resource_req=req.resource_request,
        vnf_count=req.vnf_count,
    )
    return result


@app.get("/api/history")
def get_history():
    """获取编排历史"""
    import pymysql
    import traceback
    from datetime import datetime

    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": "intelligent_orchestration",
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }

    try:
        conn = pymysql.connect(**conn_cfg)

        with conn.cursor() as c:
            try:
                c.execute("""
                    SELECT DISTINCT allocation_id, task_id, status, node_allocation, link_path, requested_resource,
                           create_time, release_time
                    FROM t_service_allocation
                    ORDER BY create_time DESC
                    LIMIT 100
                """)
            except Exception as e:
                logger.error(f"Query error: {e}")
                logger.error(traceback.format_exc())
                raise
            rows = c.fetchall()

            history = []
            for row in rows:
                node_alloc = row.get("node_allocation", "{}")
                if isinstance(node_alloc, str) and node_alloc:
                    try:
                        node_alloc = json.loads(node_alloc)
                    except:
                        node_alloc = {}

                # 获取链路分配信息
                link_path = row.get("link_path", "{}")
                if isinstance(link_path, str) and link_path:
                    try:
                        link_path = json.loads(link_path)
                    except:
                        link_path = {}

                req_resource = row.get("requested_resource", "{}")
                if isinstance(req_resource, str) and req_resource:
                    try:
                        req_resource = json.loads(req_resource)
                    except:
                        req_resource = {}

                node_ids = []
                node_alloc = node_alloc or {}
                for rid in node_alloc.keys() or []:
                    if rid.startswith("edge-"):
                        # 新格式：保持 "edge-1" 字符串
                        node_ids.append(rid)
                    elif rid.startswith("node-"):
                        try:
                            node_ids.append(int(rid.replace("node-", "")))
                        except:
                            pass

                history.append({
                    "allocation_id": row.get("allocation_id"),
                    "task_id": row.get("task_id"),
                    "status": row.get("status"),
                    "vnf_node": node_ids,
                    "node_allocation": node_alloc,
                    "link_path": link_path,
                    "qos_vector": [req_resource.get("vcpu", 0) if req_resource else 0, req_resource.get("memory", 0) if req_resource else 0],
                    "reward": None,
                    "release_time": str(row.get("release_time", "")) if row.get("release_time") else None,
                    "create_time": str(row.get("create_time", "")),
                })
            return history
    except Exception as e:
        logger.error(f"get_history error: {e}")
        logger.error(traceback.format_exc())
        raise


class DeleteHistoryRequest(BaseModel):
    allocation_id: int


@app.delete("/api/history")
def delete_history(req: DeleteHistoryRequest):
    """删除历史记录"""
    import pymysql

    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": "intelligent_orchestration",
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }
    conn = pymysql.connect(**conn_cfg)

    try:
        with conn.cursor() as c:
            # 只允许删除非活跃状态的历史记录
            c.execute("""
                DELETE FROM t_service_allocation
                WHERE allocation_id = %s AND status != 'active'
            """, (req.allocation_id,))
            conn.commit()

            if c.rowcount > 0:
                return {"status": "success", "message": "记录已删除"}
            else:
                return {"status": "failed", "error": "记录不存在或仍处于活跃状态"}
    except Exception as e:
        logger.error(f"delete_history error: {e}")
        return {"status": "failed", "error": str(e)}
    finally:
        conn.close()


class ReleaseRequest(BaseModel):
    allocation_id: int


@app.post("/api/release")
def release_allocation(req: ReleaseRequest):
    """手动释放资源分配"""
    import pymysql

    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": "intelligent_orchestration",
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }
    conn = pymysql.connect(**conn_cfg)

    try:
        conn.begin()
        with conn.cursor() as c:
            # 查找分配
            c.execute("""
                SELECT allocation_id, node_allocation, link_path, status
                FROM t_service_allocation
                WHERE allocation_id = %s AND status = 'active'
            """, (req.allocation_id,))
            row = c.fetchone()

            if not row:
                return {"status": "error", "message": "分配不存在或已释放"}

            node_alloc = row.get("node_allocation", {})
            if isinstance(node_alloc, str):
                node_alloc = json.loads(node_alloc)

            link_path = row.get("link_path", {})
            if isinstance(link_path, str):
                link_path = json.loads(link_path)

            # 释放节点资源：UPDATE t_node_compute SET used_cpu_cores = used_cpu_cores - %s
            for resource_id, usage in node_alloc.items():
                if isinstance(usage, dict):
                    release_vcpu = usage.get("vcpu", 0)
                    release_memory = usage.get("memory_gb", 0)

                    # 处理 edge-* 格式的节点ID
                    node_id = resource_id
                    if node_id.startswith("node-"):
                        node_id = node_id.replace("node-", "edge-")

                    # UPDATE t_node_compute
                    c.execute(
                        "UPDATE t_node_compute SET used_cpu_cores = MAX(0, used_cpu_cores - %s) WHERE node_id=%s",
                        (release_vcpu, node_id)
                    )
                    # UPDATE t_node_storage
                    c.execute(
                        "UPDATE t_node_storage SET used_ram_gb = MAX(0, used_ram_gb - %s) WHERE node_id=%s",
                        (release_memory, node_id)
                    )

            # 释放链路资源：UPDATE t_physical_link SET used_bandwidth_mbps = used_bandwidth_mbps - %s
            for link_idx, link_info in link_path.items():
                link_id = link_info.get("link_id")
                release_bw = link_info.get("bandwidth", 0.1)
                if link_id is not None:
                    # 处理 edge-* 格式
                    src_node = link_info.get("src_node", "")
                    dst_node = link_info.get("dst_node", "")
                    if src_node.startswith("edge-") and dst_node.startswith("edge-"):
                        link_id = f"link-{src_node}-{dst_node}"
                    elif link_id.startswith("link-"):
                        link_id = link_id

                    c.execute(
                        "UPDATE t_physical_link SET used_bandwidth_mbps = MAX(0, used_bandwidth_mbps - %s) WHERE link_id=%s",
                        (release_bw, link_id)
                    )

            # 更新状态
            c.execute("""
                UPDATE t_service_allocation
                SET status='released', release_time=NOW()
                WHERE allocation_id=%s
            """, (req.allocation_id,))

            conn.commit()
            return {"status": "success", "message": "资源已释放"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


@app.post("/api/release/auto")
def auto_release():
    """自动释放已到期的资源分配"""
    import pymysql

    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": "intelligent_orchestration",
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }
    conn = pymysql.connect(**conn_cfg)

    try:
        conn.begin()
        with conn.cursor() as c:
            # 查找所有已到期的active分配
            c.execute("""
                SELECT allocation_id, node_allocation, link_path, status, release_time
                FROM t_service_allocation
                WHERE status = 'active' AND release_time IS NOT NULL AND release_time <= NOW()
            """)
            rows = c.fetchall()

            released_count = 0
            for row in rows:
                allocation_id = row["allocation_id"]
                node_alloc = row.get("node_allocation", {})
                if isinstance(node_alloc, str):
                    node_alloc = json.loads(node_alloc)

                # 释放节点资源：UPDATE t_node_compute SET used_cpu_cores = used_cpu_cores - %s
                for resource_id, usage in node_alloc.items():
                    if isinstance(usage, dict):
                        release_vcpu = usage.get("vcpu", 0)
                        release_memory = usage.get("memory_gb", 0)

                        # 处理 edge-* 格式的节点ID
                        node_id = resource_id
                        if node_id.startswith("node-"):
                            node_id = node_id.replace("node-", "edge-")

                        # UPDATE t_node_compute
                        c.execute(
                            "UPDATE t_node_compute SET used_cpu_cores = MAX(0, used_cpu_cores - %s) WHERE node_id=%s",
                            (release_vcpu, node_id)
                        )
                        # UPDATE t_node_storage
                        c.execute(
                            "UPDATE t_node_storage SET used_ram_gb = MAX(0, used_ram_gb - %s) WHERE node_id=%s",
                            (release_memory, node_id)
                        )

                # 释放链路资源：UPDATE t_physical_link SET used_bandwidth_mbps
                link_path = row.get("link_path", {})
                if isinstance(link_path, str):
                    try:
                        link_path = json.loads(link_path)
                    except:
                        link_path = {}

                for link_idx, link_info in link_path.items():
                    link_id = link_info.get("link_id")
                    release_bw = link_info.get("bandwidth", 0.1)
                    if link_id is not None:
                        # 处理 edge-* 格式
                        src_node = link_info.get("src_node", "")
                        dst_node = link_info.get("dst_node", "")
                        if src_node.startswith("edge-") and dst_node.startswith("edge-"):
                            link_id = f"link-{src_node}-{dst_node}"
                        elif link_id.startswith("link-"):
                            link_id = link_id

                        c.execute(
                            "UPDATE t_physical_link SET used_bandwidth_mbps = MAX(0, used_bandwidth_mbps - %s) WHERE link_id=%s",
                            (release_bw, link_id)
                        )

                # 更新状态
                c.execute("""
                    UPDATE t_service_allocation
                    SET status='released', release_time=NOW()
                    WHERE allocation_id=%s
                """, (allocation_id,))
                released_count += 1

            conn.commit()
            logger.info(f"auto_release: released {released_count} allocations")
            return {"status": "success", "released": released_count}
    except Exception as e:
        conn.rollback()
        logger.error(f"auto_release failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


# ========== 启动函数 ==========

def main():
    import uvicorn
    logger.info("启动API网关: http://localhost:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()