"""
智能编排微服务
从编排队列消费请求，调用PPO决策，写数据库，将结果发回结果队列（供网关轮询）
支持资源操作（增/删节点和链路）
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 环境变量支持 Docker 部署
MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "QosRoot@123")

import pymysql
import numpy as np
import json
from datetime import datetime

from intelligent_orchestration.ppo_agent import GraphPPOOrchestrator
from intelligent_orchestration.vne_env import GraphVNEEnv
from common.message_queue import MessageQueue, get_message_queue
from common.utils import init_logger

logger = init_logger()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_mysql_connection(database="intelligent_orchestration", cursor_class=None):
    import pymysql
    conn_cfg = {
        "host": MYSQL_HOST, "port": MYSQL_PORT,
        "user": MYSQL_USER, "password": MYSQL_PASSWORD,
        "database": database, "charset": "utf8mb4"
    }
    if cursor_class:
        conn_cfg["cursorclass"] = cursor_class
    return pymysql.connect(**conn_cfg)


def write_orchestration_log(data_id: int, risk_snapshot: dict, decision_plan: dict, expected_reward: float):
    """将编排结果写入 intelligent_orchestration 数据库"""
    conn = get_mysql_connection()
    with conn.cursor() as c:
        c.execute(
            "INSERT INTO t_orchestration_log (data_id, risk_snapshot, decision_plan, expected_reward, create_time) "
            "VALUES (%s, %s, %s, %s, %s)",
            (data_id, json.dumps(risk_snapshot, ensure_ascii=False),
             json.dumps(decision_plan, ensure_ascii=False),
             float(expected_reward), datetime.now())
        )
        conn.commit()
        log_id = int(c.lastrowid)
    conn.close()
    return log_id


# ========== 资源操作处理函数 ==========

def register_resource_operation(operation_type: str, resource_id: str, operator: str = None) -> int:
    """在数据库中记录资源操作（待处理状态）"""
    conn = get_mysql_connection(cursor_class=pymysql.cursors.DictCursor)
    with conn.cursor() as c:
        c.execute(
            "INSERT INTO t_resource_operations (operation_type, resource_id, operator, status) VALUES (%s, %s, %s, %s)",
            (operation_type, resource_id, operator, "pending")
        )
        conn.commit()
        operation_id = int(c.lastrowid)
    conn.close()
    return operation_id


def complete_resource_operation(operation_id: int, status: str, error_message: str = None):
    """标记资源操作完成"""
    conn = get_mysql_connection()
    with conn.cursor() as c:
        c.execute(
            "UPDATE t_resource_operations SET status=%s, error_message=%s, complete_time=%s WHERE operation_id=%s",
            (status, error_message, datetime.now(), operation_id)
        )
        conn.commit()
    conn.close()


def add_node_to_database(node_data: dict, operator: str = None) -> str:
    """添加节点到数据库（编排服务执行）"""
    conn = get_mysql_connection(cursor_class=pymysql.cursors.DictCursor)
    with conn.cursor() as c:
        # 获取当前最大node_id
        c.execute("SELECT resource_id FROM t_resource_inventory WHERE resource_type='compute' ORDER BY resource_id DESC LIMIT 1")
        row = c.fetchone()
        if row:
            last_id = int(row['resource_id'].split('-')[1])
            new_id = last_id + 1
        else:
            new_id = 0

        resource_id = f"node-{new_id}"
        resource_name = f"计算节点-{new_id}"
        current_state = {
            "cpu": node_data.get("cpu", 0.5),
            "memory": node_data.get("memory", 0.5),
            "energy_consumption": node_data.get("energy_consumption", 0.5),
            "vcpu": node_data.get("vcpu", 16),
            "memory_gb": node_data.get("memory_gb", 32),
            "storage": node_data.get("storage", 500),
            "bandwidth": node_data.get("bandwidth", 10000)
        }

        c.execute(
            "INSERT INTO t_resource_inventory (resource_id, resource_name, resource_type, is_active, current_state) VALUES (%s, %s, %s, %s, %s)",
            (resource_id, resource_name, "compute", 1, json.dumps(current_state))
        )
        conn.commit()
    conn.close()
    return resource_id


def delete_node_from_database(resource_id: str, operator: str = None) -> bool:
    """从数据库删除节点（编排服务执行）"""
    conn = get_mysql_connection()
    with conn.cursor() as c:
        # 获取删除前的状态（用于日志）
        c.execute("SELECT current_state FROM t_resource_inventory WHERE resource_id=%s", (resource_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return False

        previous_state = row[0]
        # 记录删除前的状态
        c.execute(
            "INSERT INTO t_resource_operations (operation_type, resource_id, previous_state, operator, status, complete_time) VALUES (%s, %s, %s, %s, %s, %s)",
            ("delete_node", resource_id, previous_state, operator, "completed", datetime.now())
        )
        # 执行删除（设置为非活跃，非物理删除）
        c.execute("UPDATE t_resource_inventory SET is_active=0 WHERE resource_id=%s", (resource_id,))
        conn.commit()
    conn.close()
    return True


def add_link_to_database(link_data: dict, operator: str = None) -> str:
    """添加链路到数据库（编排服务执行）"""
    conn = get_mysql_connection(cursor_class=pymysql.cursors.DictCursor)
    with conn.cursor() as c:
        # 获取当前最大link_id
        c.execute("SELECT resource_id FROM t_resource_inventory WHERE resource_type='network' ORDER BY resource_id DESC LIMIT 1")
        row = c.fetchone()
        if row:
            last_id = int(row['resource_id'].split('-')[1])
            new_id = last_id + 1
        else:
            new_id = 0

        resource_id = f"link-{new_id}"
        src = link_data.get("src_node")
        dst = link_data.get("dst_node")
        resource_name = f"链路-{new_id} (节点{src}-节点{dst})"
        current_state = {
            "bandwidth": link_data.get("bandwidth", 1.0),
            "latency": link_data.get("latency", 10),
            "path_id": link_data.get("path_id", 0),
            "src": src,
            "dst": dst
        }

        c.execute(
            "INSERT INTO t_resource_inventory (resource_id, resource_name, resource_type, is_active, current_state) VALUES (%s, %s, %s, %s, %s)",
            (resource_id, resource_name, "network", 1, json.dumps(current_state))
        )
        conn.commit()
    conn.close()
    return resource_id


def delete_link_from_database(resource_id: str, operator: str = None) -> bool:
    """从数据库删除链路（编排服务执行）"""
    conn = get_mysql_connection()
    with conn.cursor() as c:
        c.execute("SELECT current_state FROM t_resource_inventory WHERE resource_id=%s", (resource_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return False

        previous_state = row[0]
        c.execute(
            "INSERT INTO t_resource_operations (operation_type, resource_id, previous_state, operator, status, complete_time) VALUES (%s, %s, %s, %s, %s, %s)",
            ("delete_link", resource_id, previous_state, operator, "completed", datetime.now())
        )
        c.execute("UPDATE t_resource_inventory SET is_active=0 WHERE resource_id=%s", (resource_id,))
        conn.commit()
    conn.close()
    return True


def process_resource_operation(message: dict) -> dict:
    """处理资源操作消息"""
    task_id = message.get("task_id", "unknown")
    operation_type = message.get("operation_type")
    resource_data = message.get("resource_data", {})
    resource_id = message.get("resource_id")  # 删除时使用
    operator = message.get("operator", "api")

    logger.info(f"[{task_id}] 开始处理资源操作: {operation_type}")

    result = {"task_id": task_id, "operation_type": operation_type}

    try:
        if operation_type == "add_node":
            new_resource_id = add_node_to_database(resource_data, operator)
            result["status"] = "success"
            result["resource_id"] = new_resource_id
            result["message"] = f"节点 {new_resource_id} 添加成功"

        elif operation_type == "delete_node":
            success = delete_node_from_database(resource_id, operator)
            if success:
                result["status"] = "success"
                result["resource_id"] = resource_id
                result["message"] = f"节点 {resource_id} 已删除"
            else:
                result["status"] = "failed"
                result["message"] = f"节点 {resource_id} 不存在"

        elif operation_type == "add_link":
            new_resource_id = add_link_to_database(resource_data, operator)
            result["status"] = "success"
            result["resource_id"] = new_resource_id
            result["message"] = f"链路 {new_resource_id} 添加成功"

        elif operation_type == "delete_link":
            success = delete_link_from_database(resource_id, operator)
            if success:
                result["status"] = "success"
                result["resource_id"] = resource_id
                result["message"] = f"链路 {resource_id} 已删除"
            else:
                result["status"] = "failed"
                result["message"] = f"链路 {resource_id} 不存在"

        else:
            result["status"] = "failed"
            result["message"] = f"未知操作类型: {operation_type}"

    except Exception as e:
        logger.error(f"[{task_id}] 资源操作失败: {str(e)}")
        result["status"] = "failed"
        result["message"] = str(e)

    return result


class OrchestrationService:
    def __init__(self):
        self.mq = get_message_queue()
        self.env = GraphVNEEnv()
        self.ppo_agent = GraphPPOOrchestrator(self.env)

        # PPO预热（已训练则跳过）
        import os as _os
        ppo_path = _os.path.join(BASE_DIR, "ppo_graph_model.zip")
        if not _os.path.exists(ppo_path):
            logger.info("PPO模型不存在，进行预热训练...")
            self.ppo_agent.train_warmup(timesteps=10000)
            logger.info("PPO模型训练完成")
        else:
            logger.info("PPO模型已存在，跳过训练")

        logger.info("智能编排服务初始化完成")

    def process(self, message: dict) -> dict:
        task_id = message.get("task_id", "unknown")
        data_id = message.get("data_id", 0)
        qos_vector = message.get("qos_vector", [0.8, 0.1, 1.0, 0.05])
        resource_req = message.get("resource_request", {})

        logger.info(f"[{task_id}] 开始编排, QoS向量: {qos_vector}")

        try:
            # 1. 设置QoS到VNR
            qos_arr = np.array(qos_vector, dtype=np.float32)
            obs, _ = self.env.reset()
            self.env.set_qos_to_vnr(qos_arr)
            obs = self.env._get_obs()

            # 2. PPO决策
            action_dict = self.ppo_agent.predict(obs)

            if not isinstance(action_dict, dict) or "vnf_node" not in action_dict:
                raise ValueError(f"动作格式异常: {action_dict}")

            logger.info(f"[{task_id}] VNF映射={action_dict['vnf_node']}")

            # 3. 执行动作
            obs, reward, terminated, truncated, info = self.env.step(action_dict)

            # 4. 写数据库（intelligent_orchestration）
            decision_plan = {
                "vnf_node": [int(n) for n in action_dict["vnf_node"]],
                "qos_vector": qos_vector,
            }
            risk_snapshot = {
                "anomaly_score": message.get("anomaly_score", 0),
                "risk_level": message.get("risk_level", "NORMAL"),
            }
            log_id = write_orchestration_log(data_id, risk_snapshot, decision_plan, float(reward))
            logger.info(f"[{task_id}] 编排日志写入: log_id={log_id}")

            # 5. 构造完整结果，发到结果队列（供网关轮询）
            result = {
                "status": "success",
                "task_id": task_id,
                "data_id": data_id,
                "log_id": log_id,
                "anomaly_score": message.get("anomaly_score", 0),
                "risk_level": message.get("risk_level", "NORMAL"),
                "qos_vector": qos_vector,
                "vnf_node": [int(n) for n in action_dict["vnf_node"]],
                "link_path": [],  # 链路通过BFS自动计算，不再输出
                "reward": round(float(reward), 2),
                "qos_ok": bool(info.get("qos_ok")),
                "resource_ok": bool(info.get("resource_ok")),
                "resource_plan": resource_req,
            }
            self.mq.push_result(task_id, result)
            logger.info(f"[{task_id}] 结果已发送到结果队列")

            return result

        except Exception as e:
            logger.error(f"[{task_id}] 处理失败: {str(e)}", exc_info=True)
            err_result = {
                "status": "error",
                "task_id": task_id,
                "error": str(e),
            }
            self.mq.push_result(task_id, err_result)
            return err_result

    def run(self):
        logger.info("智能编排服务启动，等待消息...")

        while True:
            try:
                # 优先处理编排队列消息
                message = self.mq.consume(MessageQueue.QUEUE_ORCHESTRATION, blocking=True, timeout=30)
                if message:
                    self.process(message)
                    continue

                # 处理资源操作队列消息
                try:
                    import redis
                    res_msg = self.mq.redis_client.blpop(MessageQueue.QUEUE_RESOURCE_OPS, timeout=1)
                    if res_msg:
                        _, data = res_msg
                        message = json.loads(data)
                        result = process_resource_operation(message)
                        self.mq.push_result(message.get("task_id"), result)
                except Exception:
                    pass  # 队列为空时继续等待

            except KeyboardInterrupt:
                logger.info("收到中断信号，停止服务")
                break
            except Exception as e:
                logger.error(f"服务异常: {str(e)}", exc_info=True)


def main():
    logger.info("=" * 30)
    logger.info("启动智能编排微服务")
    logger.info("=" * 30)
    service = OrchestrationService()
    service.run()


if __name__ == "__main__":
    main()