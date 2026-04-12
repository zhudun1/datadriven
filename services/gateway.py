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

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from common.message_queue import MessageQueue, get_message_queue
from common.utils import init_logger

logger = init_logger()

app = FastAPI(title="数据驱动编排系统 API", version="1.0.0")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 数据模型 ==========

class OrchRequest(BaseModel):
    businessImagePath: str
    pointCloudPath: str
    resourceRequest: Optional[dict] = {}


class AddNodeRequest(BaseModel):
    cpu: float
    memory: float
    energy_consumption: float
    vcpu: int
    memory_gb: int
    storage: int
    bandwidth: int


class AddLinkRequest(BaseModel):
    src_node: int
    dst_node: int
    bandwidth: float
    latency: float
    path_id: int


# ========== 数据库写入工具 ==========

def write_industrial_data(rgb_path: str, pcd_path: str) -> int:
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
            "INSERT INTO t_industrial_data (rgb_path, pcd_path, create_time) VALUES (%s, %s, %s)",
            (rgb_path, pcd_path, datetime.now())
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

        # 1. 写数据库（business_awareness）
        data_id = write_industrial_data(req.businessImagePath, req.pointCloudPath)
        logger.info(f"工业数据写入成功: data_id={data_id}")

        # 2. 发消息到感知队列
        message = {
            "task_id": str(data_id),
            "data_id": data_id,
            "rgb_path": req.businessImagePath,
            "pcd_path": req.pointCloudPath,
            "resource_request": req.resourceRequest or {}
        }
        mq.publish(MessageQueue.QUEUE_PERCEPTION, message)
        logger.info(f"任务已提交到感知队列: task_id={message['task_id']}")

        # 3. 轮询等待结果（最多等待 120 秒）
        result = mq.get_result(message["task_id"], timeout=120)

        if result is None:
            return {"status": "timeout", "message": "处理超时"}

        return result

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
    with conn.cursor() as c:
        c.execute("SELECT * FROM t_resource_inventory WHERE is_active=1")
        rows = c.fetchall()
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

    return {"resources": json_safe(rows)}


def submit_resource_operation(operation_type: str, resource_data: dict = None, resource_id: str = None) -> dict:
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
                # 获取最大ID
                c.execute("SELECT resource_id FROM t_resource_inventory WHERE resource_type='compute' ORDER BY resource_id DESC LIMIT 1")
                row = c.fetchone()
                new_id = int(row['resource_id'].split('-')[1]) + 1 if row else 0

                resource_id = f"node-{new_id}"
                current_state = {
                    "cpu": resource_data.get("cpu", 0.5),
                    "memory": resource_data.get("memory", 0.5),
                    "energy_consumption": resource_data.get("energy_consumption", 0.5),
                    "vcpu": resource_data.get("vcpu", 16),
                    "memory_gb": resource_data.get("memory_gb", 32),
                    "storage": resource_data.get("storage", 500),
                    "bandwidth": resource_data.get("bandwidth", 10000)
                }

                c.execute(
                    "INSERT INTO t_resource_inventory (resource_id, resource_name, resource_type, is_active, current_state) VALUES (%s, %s, %s, %s, %s)",
                    (resource_id, f"计算节点-{new_id}", "compute", 1, json.dumps(current_state))
                )
                conn.commit()
                return {"status": "success", "resource_id": resource_id, "message": f"节点 {resource_id} 添加成功"}

            elif operation_type == "add_link":
                c.execute("SELECT resource_id FROM t_resource_inventory WHERE resource_type='network' ORDER BY resource_id DESC LIMIT 1")
                row = c.fetchone()
                new_id = int(row['resource_id'].split('-')[1]) + 1 if row else 0

                resource_id = f"link-{new_id}"
                src = resource_data.get("src_node", 0)
                dst = resource_data.get("dst_node", 1)
                current_state = {
                    "bandwidth": resource_data.get("bandwidth", 1.0),
                    "latency": resource_data.get("latency", 10),
                    "path_id": resource_data.get("path_id", 0),
                    "src": src,
                    "dst": dst
                }

                c.execute(
                    "INSERT INTO t_resource_inventory (resource_id, resource_name, resource_type, is_active, current_state) VALUES (%s, %s, %s, %s, %s)",
                    (resource_id, f"链路-{new_id} (节点{src}-节点{dst})", "network", 1, json.dumps(current_state))
                )
                conn.commit()
                return {"status": "success", "resource_id": resource_id, "message": f"链路 {resource_id} 添加成功"}

            elif operation_type == "delete_node" or operation_type == "delete_link":
                c.execute("UPDATE t_resource_inventory SET is_active=0 WHERE resource_id=%s", (resource_id,))
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

    result = submit_resource_operation("add_node", resource_data=resource_data)

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

    result = submit_resource_operation("add_link", resource_data=resource_data)

    if result.get("status") != "success":
        raise HTTPException(status_code=500, detail=result.get("message", "添加链路失败"))

    return {"status": "success", "resource_id": result.get("resource_id"), "message": result.get("message")}


@app.delete("/resources/{resource_id}")
def delete_resource(resource_id: str, authorization: Optional[str] = Header(None)):
    """删除物理资源（通过消息队列）"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    if resource_id.startswith("node-"):
        operation_type = "delete_node"
    elif resource_id.startswith("link-"):
        operation_type = "delete_link"
    else:
        raise HTTPException(status_code=400, detail="无效的资源ID")

    result = submit_resource_operation(operation_type, resource_id=resource_id)

    if result.get("status") != "success":
        raise HTTPException(status_code=500, detail=result.get("message", "删除资源失败"))

    return {"status": "success", "resource_id": resource_id, "message": result.get("message")}


# ========== 启动函数 ==========

def main():
    import uvicorn
    logger.info("启动API网关: http://localhost:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()