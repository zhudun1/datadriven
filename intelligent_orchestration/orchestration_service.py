"""
Orchestration微服务 - 整合版
支持：
1. 节点资源可运行多条服务（非独占）
2. 并发处理多个编排请求（多进程worker）
3. 资源持有时间：编排完成后保留1分钟
4. 请求前可查看资源剩余量
5. 资源不足时智能等待（5秒内释放的资源可等待）
"""
from typing import Optional
import sys
import os
import multiprocessing as mp
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Docker环境变量（兼容原配置）
QOS_MYSQL_HOST = os.environ.get("QOS_MYSQL_HOST", os.environ.get("MYSQL_HOST", "127.0.0.1"))
QOS_MYSQL_PORT = int(os.environ.get("QOS_MYSQL_PORT", os.environ.get("MYSQL_PORT", "3306")))
QOS_MYSQL_USER = os.environ.get("QOS_MYSQL_USER", os.environ.get("MYSQL_USER", "root"))
QOS_MYSQL_PASSWORD = os.environ.get("QOS_MYSQL_PASSWORD", os.environ.get("MYSQL_PASSWORD", "QosRoot@123"))

import pymysql
import numpy as np
import json
from datetime import datetime, timedelta

# 导入原代码
from intelligent_orchestration.ppo_agent import GraphPPOOrchestrator
from intelligent_orchestration.vne_env import GraphVNEEnv
from common.utils import init_logger

logger = init_logger()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_ALLOCATION_TABLE = "t_service_allocation"

# 默认配置
DEFAULT_RESOURCE_HOLD_SECONDS = 60      # 资源持有60秒
DEFAULT_RELEASE_BATCH_SIZE = 100       # 批量释放大小
DEFAULT_RESOURCE_WAIT_SECONDS = 5       # 等待超时5秒
DEFAULT_RESOURCE_WAIT_POLL_INTERVAL = 1.0  # 轮询间隔


def get_mysql_connection(database="intelligent_orchestration", cursor_class=None):
    """获取MySQL连接"""
    conn_cfg = {
        "host": QOS_MYSQL_HOST, "port": QOS_MYSQL_PORT,
        "user": QOS_MYSQL_USER, "password": QOS_MYSQL_PASSWORD,
        "database": database, "charset": "utf8mb4"
    }
    if cursor_class:
        conn_cfg["cursorclass"] = cursor_class
    return pymysql.connect(**conn_cfg)


def _to_float(value, default: float = 0.0) -> float:
    """安全转换为浮点数"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_node_id(node_id: int) -> str:
    """将节点ID规范化为字符串格式: 0-2->edge-1-3, 3-4->cloud-1-2"""
    if node_id >= 3:
        return f"cloud-{node_id - 2}"  # 3->cloud-1, 4->cloud-2
    return f"edge-{node_id + 1}"  # 0->edge-1, 1->edge-2, 2->edge-3


def _normalize_resource_request(resource_req: dict) -> dict:
    """标准化资源请求 - 每个VNF消耗2GB内存，1个vCPU"""
    req = resource_req if isinstance(resource_req, dict) else {}
    return {
        "vcpu": max(0.0, _to_float(req.get("vcpu", 1), 1)),
        "memory": max(0.0, _to_float(req.get("memory", 2), 2)),
        "storage": max(0.0, _to_float(req.get("storage", 10), 10)),
        "bandwidth": max(0.0, _to_float(req.get("bandwidth", 0.5), 0.5)),
        "latency": max(0.0, _to_float(req.get("latency", 5), 5)),
    }


def write_orchestration_log(data_id: int, risk_snapshot: dict, decision_plan: dict, expected_reward: float):
    """写入编排日志"""
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


# ========== 资源租约管理 ==========

class ResourceLeaseManager:
    """资源租约管理器 - 整合版"""

    def __init__(self,
                 hold_seconds: int = DEFAULT_RESOURCE_HOLD_SECONDS,
                 release_batch_size: int = DEFAULT_RELEASE_BATCH_SIZE,
                 wait_seconds: int = DEFAULT_RESOURCE_WAIT_SECONDS,
                 poll_interval: float = DEFAULT_RESOURCE_WAIT_POLL_INTERVAL):
        self.hold_seconds = hold_seconds
        self.release_batch_size = release_batch_size
        self.wait_seconds = wait_seconds
        self.poll_interval = poll_interval
        self._ensure_table()

    def _ensure_table(self):
        """确保服务分配表存在"""
        conn = get_mysql_connection()
        try:
            with conn.cursor() as c:
                c.execute(f"""
                    CREATE TABLE IF NOT EXISTS {SERVICE_ALLOCATION_TABLE} (
                        allocation_id BIGINT PRIMARY KEY AUTO_INCREMENT,
                        task_id VARCHAR(64) NOT NULL UNIQUE,
                        data_id BIGINT DEFAULT NULL,
                        status ENUM('active','released','failed') DEFAULT 'active',
                        node_allocation JSON DEFAULT NULL,
                        requested_resource JSON DEFAULT NULL,
                        create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        release_time TIMESTAMP NULL DEFAULT NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                try:
                    c.execute(f"""
                        CREATE INDEX idx_service_allocation_status_time
                        ON {SERVICE_ALLOCATION_TABLE}(status, create_time)
                    """)
                except Exception:
                    pass
                conn.commit()
        finally:
            conn.close()

    def reserve_node_resources(self, task_id: str, data_id: int, node_demands: dict, resource_req: dict, link_path: dict = None) -> tuple[bool, dict]:
        """预留节点资源（使用乐观锁减少锁冲突）"""
        if not node_demands:
            return True, {"allocation_id": None, "node_allocation": {}}

        # 获取保持时间（默认60秒）
        hold_seconds = self.hold_seconds
        # 链路分配信息（默认为空）
        link_path_data = link_path or {}

        resource_ids = sorted(set(node_demands.keys()))

        conn = get_mysql_connection(cursor_class=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as c:
                # 不使用 FOR UPDATE（减少锁冲突），直接查询
                placeholders = ",".join(["%s"] * len(resource_ids))
                c.execute(f"""
                    SELECT pn.node_id, pn.node_name, nc.cpu_cores, nc.used_cpu_cores,
                           ns.ram_gb, ns.used_ram_gb
                    FROM t_physical_node pn
                    JOIN t_node_compute nc ON pn.node_id = nc.node_id
                    JOIN t_node_storage ns ON pn.node_id = ns.node_id
                    WHERE pn.node_id IN ({placeholders})
                      AND pn.is_active = 1
                """, tuple(resource_ids))
                rows = c.fetchall()
                row_map = {row["node_id"]: row for row in rows}

                # 检查资源存在
                for resource_id in resource_ids:
                    if resource_id not in row_map:
                        conn.rollback()
                        return False, {"error": f"resource_not_found:{resource_id}"}

                allocation_snapshot = {}
                for resource_id in resource_ids:
                    row = row_map[resource_id]
                    # 使用字符串resource_id查询demand（与node_demands的key一致）
                    demand = node_demands.get(resource_id, {"vcpu": 0.0, "memory_gb": 0.0})

                    # 提取资源信息（非独占：累加已使用量）
                    total_vcpu = _to_float(row.get("cpu_cores", 0.0), 0.0)
                    total_memory = _to_float(row.get("ram_gb", 0.0), 0.0)
                    used_vcpu = _to_float(row.get("used_cpu_cores", 0.0), 0.0)
                    used_memory = _to_float(row.get("used_ram_gb", 0.0), 0.0)
                    need_vcpu = _to_float(demand.get("vcpu", 0.0), 0.0)
                    need_memory = _to_float(demand.get("memory_gb", 0.0), 0.0)

                    # 计算剩余
                    free_vcpu = total_vcpu - used_vcpu
                    free_memory = total_memory - used_memory

                    # 检查是否足够（非独占：允许多服务累加）
                    if free_vcpu < need_vcpu or free_memory < need_memory:
                        conn.rollback()
                        return False, {
                            "error": "resource_insufficient",
                            "resource_id": resource_id,
                            "need": {"vcpu": round(need_vcpu, 4), "memory_gb": round(need_memory, 4)},
                            "free": {"vcpu": round(max(free_vcpu, 0.0), 4), "memory_gb": round(max(free_memory, 0.0), 4)},
                        }

                    new_used_vcpu = round(used_vcpu + need_vcpu, 4)
                    new_used_memory = round(used_memory + need_memory, 4)
                    new_allocations = int(row.get("allocations", 0) or 0) + 1

                    # UPDATE t_node_compute 表
                    c.execute("""
                        UPDATE t_node_compute
                        SET used_cpu_cores = %s, allocations = %s
                        WHERE node_id = %s
                    """, (new_used_vcpu, new_allocations, resource_id))
                    logger.info(f"UPDATE compute SET used_cpu_cores={new_used_vcpu}(old={used_vcpu}+need={need_vcpu}) WHERE node_id={resource_id}, rows={c.rowcount}")

                    # UPDATE t_node_storage 表
                    c.execute("""
                        UPDATE t_node_storage
                        SET used_ram_gb = %s
                        WHERE node_id = %s
                    """, (new_used_memory, resource_id))
                    logger.info(f"UPDATE storage SET used_ram_gb={new_used_memory}(old={used_memory}+need={need_memory}) WHERE node_id={resource_id}, rows={c.rowcount}")

                    allocation_snapshot[resource_id] = {
                        "node_id": resource_id,
                        "vcpu": round(need_vcpu, 4),
                        "memory_gb": round(need_memory, 4),
                    }

                # 插入分配记录（包含release_time和link_path）
                release_delay = hold_seconds  # 使用类属性
                c.execute(f"""
                    INSERT INTO {SERVICE_ALLOCATION_TABLE}
                    (task_id, data_id, status, node_allocation, requested_resource, link_path, release_time)
                    VALUES (%s, %s, %s, %s, %s, %s, TIMESTAMPADD(SECOND, %s, NOW()))
                """, (
                    task_id,
                    data_id,
                    "active",
                    json.dumps(allocation_snapshot, ensure_ascii=False),
                    json.dumps(_normalize_resource_request(resource_req), ensure_ascii=False),
                    json.dumps(link_path_data, ensure_ascii=False),  # 使用传入的链路信息
                    release_delay,
                ))
                allocation_id = int(c.lastrowid)

                logger.info(f"[{task_id}] reserve_node_resources: link_path_data={link_path_data}")

                # 更新链路资源（分配带宽）
                if link_path_data:
                    for link_idx, link_info in link_path_data.items():
                        link_id = link_info.get("link_id")
                        bw_demand = link_info.get("bandwidth", 0.1)
                        src = link_info.get("src")
                        dst = link_info.get("dst")

                        # 如果link_id不是有效字符串，通过src/dst索引查询
                        if not link_id or link_id == "null":
                            # 通过节点索引查询link_id
                            index_to_node = {0: 'edge-1', 1: 'edge-2', 2: 'edge-3', 3: 'cloud-1', 4: 'cloud-2'}
                            src_node = index_to_node.get(src)
                            dst_node = index_to_node.get(dst)
                            if src_node and dst_node:
                                c.execute("""
                                    SELECT link_id FROM t_physical_link
                                    WHERE (src_node = %s AND dst_node = %s) OR (src_node = %s AND dst_node = %s)
                                """, (src_node, dst_node, dst_node, src_node))
                                row = c.fetchone()
                                if row:
                                    link_id = row["link_id"]

                        if link_id and link_id != "null":
                            # 获取当前使用的带宽
                            c.execute("SELECT used_bandwidth_mbps FROM t_physical_link WHERE link_id = %s", (link_id,))
                            link_row = c.fetchone()
                            current_bw = link_row["used_bandwidth_mbps"] if link_row else 0
                            new_bw = current_bw + bw_demand

                            # UPDATE t_physical_link表
                            c.execute("""
                                UPDATE t_physical_link
                                SET used_bandwidth_mbps = %s, allocations = allocations + 1
                                WHERE link_id = %s
                            """, (new_bw, link_id))
                            logger.info(f"UPDATE link SET used_bandwidth_mbps={new_bw}(old={current_bw}+need={bw_demand}) WHERE link_id={link_id}")

            conn.commit()
            # 返回带release_time的分配信息
            release_time = datetime.now() + timedelta(seconds=release_delay)
            return True, {"allocation_id": allocation_id, "node_allocation": allocation_snapshot, "release_time": release_time}
        except Exception as e:
            conn.rollback()
            return False, {"error": str(e)}
        finally:
            conn.close()

    def schedule_release(self, allocation_id: int, hold_seconds: int = None) -> None:
        """调度资源释放时间（异步执行，避免阻塞主请求）"""
        if not allocation_id:
            return

        # 启动后台线程执行释放调度，避免阻塞主请求
        import threading
        def _schedule():
            try:
                # 创建独立连接
                conn = get_mysql_connection()
                try:
                    with conn.cursor() as c:
                        delay = self.hold_seconds if hold_seconds is None else max(0, int(hold_seconds))
                        c.execute(f"""
                            UPDATE {SERVICE_ALLOCATION_TABLE}
                            SET release_time = TIMESTAMPADD(SECOND, %s, NOW())
                            WHERE allocation_id=%s AND status='active'
                        """, (delay, int(allocation_id)))
                        rows = c.rowcount
                        conn.commit()
                        if rows > 0:
                            logger.info(f"schedule_release: allocation_id={allocation_id}, delay={delay}s")
                finally:
                    conn.close()
            except Exception as e:
                logger.warning(f"schedule_release failed: {e}")

        thread = threading.Thread(target=_schedule, daemon=True)
        thread.start()

    def release_due_allocations(self, batch_size: int = None) -> int:
        """释放到期的资源分配"""
        limit = self.release_batch_size if batch_size is None else max(1, int(batch_size))
        conn = get_mysql_connection(cursor_class=pymysql.cursors.DictCursor)
        released_count = 0

        try:
            conn.begin()
            with conn.cursor() as c:
                # 查询到期的分配
                c.execute(f"""
                    SELECT allocation_id, node_allocation, link_path
                    FROM {SERVICE_ALLOCATION_TABLE}
                    WHERE status='active'
                      AND release_time IS NOT NULL
                      AND release_time <= NOW()
                    ORDER BY release_time ASC
                    LIMIT %s
                    FOR UPDATE
                """, (limit,))
                rows = c.fetchall()
                if not rows:
                    conn.commit()
                    return 0

                # 释放每个分配的资源
                for row in rows:
                    allocation_id = int(row["allocation_id"])
                    allocation = row.get("node_allocation") or {}
                    if isinstance(allocation, str):
                        try:
                            allocation = json.loads(allocation)
                        except Exception:
                            allocation = {}

                    for resource_id, usage in allocation.items():
                        usage = usage or {}
                        release_vcpu = _to_float(usage.get("vcpu", 0.0), 0.0)
                        release_memory = _to_float(usage.get("memory_gb", 0.0), 0.0)

                        # 查询并更新 t_node_compute
                        c.execute("""
                            SELECT used_cpu_cores, allocations
                            FROM t_node_compute
                            WHERE node_id = %s
                            FOR UPDATE
                        """, (resource_id,))
                        resource_row = c.fetchone()
                        if not resource_row:
                            continue

                        used_vcpu = max(0.0, _to_float(resource_row.get("used_cpu_cores", 0.0), 0.0) - release_vcpu)
                        allocations = max(0, int(resource_row.get("allocations", 0) or 0) - 1)

                        # UPDATE t_node_compute 列
                        c.execute("""
                            UPDATE t_node_compute
                            SET used_cpu_cores = %s, allocations = %s
                            WHERE node_id = %s
                        """, (round(used_vcpu, 4), allocations, resource_id))

                    # 释放链路带宽 - UPDATE t_physical_link 表
                    link_path = row.get("link_path", {})
                    if isinstance(link_path, str):
                        try:
                            link_path = json.loads(link_path)
                        except:
                            link_path = {}

                    for link_idx, link_info in link_path.items():
                        link_id = link_info.get("link_id")
                        release_bw = link_info.get("bandwidth", 0.1)
                        src = link_info.get("src_node") or link_info.get("src")
                        dst = link_info.get("dst_node") or link_info.get("dst")

                        # 如果 src/dst 是数字索引，转换为节点名称
                        src_node = None
                        dst_node = None

                        if src is not None and dst is not None:
                            if isinstance(src, int) and isinstance(dst, int):
                                # 索引到节点名称映射
                                index_to_node = {0: 'edge-1', 1: 'edge-2', 2: 'edge-3', 3: 'cloud-1', 4: 'cloud-2'}
                                src_node = index_to_node.get(src)
                                dst_node = index_to_node.get(dst)
                            elif isinstance(src, str) and isinstance(dst, str):
                                src_node = src
                                dst_node = dst

                        # 如果 link_id 为 null，通过 src_node 和 dst_node 查找
                        if link_id is None and src_node is not None and dst_node is not None:
                            c.execute("""
                                SELECT link_id FROM t_physical_link
                                WHERE (src_node = %s AND dst_node = %s) OR (src_node = %s AND dst_node = %s) AND is_active = 1
                            """, (src_node, dst_node, dst_node, src_node))
                            row = c.fetchone()
                            if row:
                                link_id = row['link_id']

                        if link_id is not None:
                            # 查询链路
                            c.execute("""
                                SELECT used_bandwidth_mbps, allocations
                                FROM t_physical_link
                                WHERE link_id = %s AND is_active = 1
                                FOR UPDATE
                            """, (link_id,))
                            link_row = c.fetchone()
                            if link_row:
                                current_used_bandwidth_mbps = _to_float(link_row.get("used_bandwidth_mbps", 0.0), 0.0)
                                new_used_bandwidth_mbps = max(0, current_used_bandwidth_mbps - release_bw)
                                new_allocations = max(0, int(link_row.get("allocations", 0) or 0) - 1)
                                # UPDATE t_physical_link 列
                                c.execute("""
                                    UPDATE t_physical_link
                                    SET used_bandwidth_mbps = %s, allocations = %s
                                    WHERE link_id = %s
                                """, (round(new_used_bandwidth_mbps, 4), new_allocations, link_id))

                    # 更新分配状态
                    c.execute(f"""
                        UPDATE {SERVICE_ALLOCATION_TABLE}
                        SET status='released', release_time=NOW()
                        WHERE allocation_id=%s AND status='active'
                    """, (allocation_id,))
                    released_count += int(c.rowcount or 0)

            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"release due allocations failed: {e}")
            return 0
        finally:
            conn.close()

        if released_count > 0:
            logger.info(f"released {released_count} allocation(s)")
        return released_count

    def get_seconds_until_candidate_release(self, resource_ids: list, max_wait_seconds: float) -> Optional[float]:
        """查询最近可释放的时间（用于智能等待）

        改进：先触发释放已到期的分配，然后查询
        确保使用独立的fresh连接
        """
        if not resource_ids or max_wait_seconds <= 0:
            return None

        # 先尝试释放已到期的资源（使用新连接）
        conn = get_mysql_connection(cursor_class=pymysql.cursors.DictCursor)
        try:
            conn.begin()
            with conn.cursor() as c:
                # 查询已经到期的分配（这些保证可以立即释放）
                c.execute(f"""
                    SELECT allocation_id
                    FROM {SERVICE_ALLOCATION_TABLE}
                    WHERE status='active'
                      AND release_time IS NOT NULL
                      AND release_time <= NOW()
                    LIMIT 1
                """)
                row = c.fetchone()

                # 如果有已到期的分配，立即触发释放
                if row:
                    # 释放这个分配
                    c.execute(f"""
                        SELECT allocation_id, node_allocation
                        FROM {SERVICE_ALLOCATION_TABLE}
                        WHERE status='active'
                          AND release_time IS NOT NULL
                          AND release_time <= NOW()
                        LIMIT 10
                    """)
                    rows = c.fetchall()
                    for r in rows:
                        alloc_id = int(r["allocation_id"])
                        node_alloc = r.get("node_allocation", {})
                        if isinstance(node_alloc, str):
                            import json
                            node_alloc = json.loads(node_alloc)

                        # 释放资源
                        for node_id, info in node_alloc.items():
                            vcpu = float(info.get("vcpu", 0))
                            memory_gb = float(info.get("memory_gb", 0))

                            c.execute("UPDATE t_node_compute SET used_cpu_cores = used_cpu_cores - %s, allocations = allocations - 1 WHERE node_id = %s", (vcpu, node_id))
                            c.execute("UPDATE t_node_storage SET used_ram_gb = used_ram_gb - %s WHERE node_id = %s", (memory_gb, node_id))

                        c.execute("UPDATE t_service_allocation SET status = 'released' WHERE allocation_id = %s", (alloc_id,))

                    conn.commit()
                    return 0.0  # 已释放

                # 没有已到期的，查询最近的释放时间作为参考
                c.execute(f"""
                    SELECT MIN(release_time) AS next_release_time
                    FROM {SERVICE_ALLOCATION_TABLE}
                    WHERE status='active'
                      AND release_time IS NOT NULL
                      AND release_time > NOW()
                    LIMIT 1
                """)
                row = c.fetchone() or {}
                next_release = row.get("next_release_time")
                if not next_release:
                    return None

                # 计算距离现在还有多少秒
                delta = (next_release - datetime.now()).total_seconds()
                # 释放时间在等待预算内，返回正数（等待这段时间）
                if 0 < delta <= max_wait_seconds:
                    return max(0.0, delta)
                # 释放时间超过等待预算，但存在，返回负值表示需要等待更久
                return -delta
        except Exception as e:
            logger.error(f"query candidate release failed: {e}")
            return None
        finally:
            conn.close()

    def get_available_resources(self) -> dict:
        """查看当前剩余资源量"""
        conn = get_mysql_connection(cursor_class=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as c:
                # JOIN 查询从新表
                c.execute("""
                    SELECT pn.node_id, pn.node_name,
                           nc.cpu_cores, nc.used_cpu_cores, ns.ram_gb, ns.used_ram_gb, nc.allocations,
                           ns.storage_gb, ns.used_storage_gb
                    FROM t_physical_node pn
                    JOIN t_node_compute nc ON pn.node_id = nc.node_id
                    JOIN t_node_storage ns ON pn.node_id = ns.node_id
                    WHERE pn.is_active = 1
                """)
                nodes = {}
                for row in c.fetchall():
                    node_id = int(row["node_id"].split("-")[-1]) - 1  # edge-1 -> 0
                    total_vcpu = _to_float(row.get("cpu_cores", 0.0), 0.0)
                    used_vcpu = _to_float(row.get("used_cpu_cores", 0.0), 0.0)
                    total_memory = _to_float(row.get("ram_gb", 0.0), 0.0)
                    used_memory = _to_float(row.get("used_ram_gb", 0.0), 0.0)
                    nodes[node_id] = {
                        "resource_id": row["node_id"],
                        "free_vcpu": round(total_vcpu - used_vcpu, 2),
                        "free_memory_gb": round(total_memory - used_memory, 2),
                        "total_vcpu": round(total_vcpu, 2),
                        "total_memory_gb": round(total_memory, 2),
                        "allocations": int(row.get("allocations", 0) or 0),
                    }
                return nodes
        finally:
            conn.close()

    def build_node_demands(self, action_dict: dict, resource_req: dict) -> dict:
        """构建节点资源需求"""
        normalized = _normalize_resource_request(resource_req)
        vnf_nodes = [int(n) for n in action_dict.get("vnf_node", [])]
        vnf_count = max(1, len(vnf_nodes))
        per_vnf_vcpu = normalized["vcpu"] / vnf_count
        per_vnf_memory = normalized["memory"] / vnf_count

        node_demands = {}
        for node_id in vnf_nodes:
            node_str = _normalize_node_id(node_id)  # 输出字符串节点ID: edge-1, edge-2, etc
            if node_str not in node_demands:
                node_demands[node_str] = {"vcpu": 0.0, "memory_gb": 0.0}
            node_demands[node_str]["vcpu"] += per_vnf_vcpu
            node_demands[node_str]["memory_gb"] += per_vnf_memory
        return node_demands


# ========== 编排服务 ==========

class OrchestrationService:
    """编排服务 - 使用真实的数据库资源"""

    def __init__(self, epsilon: float = 0.4):
        """
        epsilon: PPO算法的随机探索概率，默认40%
        """
        # 使用真实数据库资源训练
        self.env = GraphVNEEnv(use_db=True)
        self.ppo_agent = GraphPPOOrchestrator(self.env, epsilon=epsilon)
        self.lease_manager = ResourceLeaseManager()

        # PPO模型预热
        ppo_path = os.path.join(BASE_DIR, "ppo_graph_model.zip")
        if not os.path.exists(ppo_path):
            logger.info("PPO model not found, starting warmup training with real DB resources")
            self.ppo_agent.train_warmup(timesteps=30000)
            logger.info("PPO warmup training completed")
        else:
            logger.info("PPO model exists, skip training")

        logger.info("Orchestration service initialized (使用真实数据库资源)")

    def process(self, message: dict) -> dict:
        """处理编排请求（带资源租约和智能等待）- 复用env避免重复创建"""
        task_id = message.get("task_id", "unknown")
        data_id = message.get("data_id", 0)
        qos_vector = message.get("qos_vector", [0.8, 0.1, 1.0, 0.05])
        resource_req = message.get("resource_request", {})
        vnf_count = message.get("vnf_count", 2)
        allocation_id = None

        logger.info(f"[{task_id}] start orchestration, qos_vector={qos_vector}, vnf_count={vnf_count}")

        try:
            # 复用现有env和lease_manager，只刷新资源和更新vnf_count
            self.env.vnf_count = vnf_count
            self.env._reload_resources()

            # 业务感知 -> 智能编排
            qos_arr = np.array(qos_vector, dtype=np.float32)
            obs, _ = self.env.reset()
            self.env.set_qos_to_vnr(qos_arr)
            obs = self.env._get_obs()

            action_dict = self.ppo_agent.predict(obs)
            if not isinstance(action_dict, dict) or "vnf_node" not in action_dict:
                raise ValueError(f"invalid action format: {action_dict}")

            logger.info(f"[{task_id}] vnf mapping={action_dict['vnf_node']}")

            # 从VNE环境获取计算出的链路路径
            action_for_link = type('Action', (), {
                'vnf_node_mapping': {i: n for i, n in enumerate(action_dict['vnf_node'])},
                'link_path_mapping': {}
            })()
            self.env._compute_link_path(action_for_link)
            link_path = action_for_link.link_path_mapping
            logger.info(f"[{task_id}] link_path={link_path}")

            # 构建资源需求和链路需求
            node_demands = self.lease_manager.build_node_demands(action_dict, resource_req)
            resource_ids = list(node_demands.keys())  # 已经是字符串格式 edge-1, edge-2, etc

            # ====== 智能等待机制（最多等待5秒）======
            wait_start = time.monotonic()
            resource_waited_seconds = 0.0
            wait_attempted = False
            reservation = {}
            reserved = False

            while True:
                reserved, reservation = self.lease_manager.reserve_node_resources(
                    task_id, data_id, node_demands, resource_req, link_path
                )
                if reserved:
                    break

                if reservation.get("error") != "resource_insufficient":
                    break

                elapsed = time.monotonic() - wait_start
                remaining_wait = self.lease_manager.wait_seconds - elapsed
                if remaining_wait <= 0:
                    resource_waited_seconds = elapsed
                    reservation = {
                        **reservation,
                        "error": "resource_wait_timeout",
                        "wait_window_seconds": self.lease_manager.wait_seconds,
                        "waited_seconds": round(elapsed, 3),
                    }
                    break

                # 查询最近的资源释放时间
                seconds_to_release = self.lease_manager.get_seconds_until_candidate_release(
                    resource_ids, remaining_wait
                )

                # 处理各种情况
                if seconds_to_release is None:
                    # 没有即将释放的分配
                    resource_waited_seconds = time.monotonic() - wait_start
                    reservation = {
                        **reservation,
                        "error": "resource_wait_no_release_window",
                        "wait_window_seconds": self.lease_manager.wait_seconds,
                        "waited_seconds": round(resource_waited_seconds, 3),
                    }
                    break

                # seconds_to_release = 0 表示有已到期的分配，立即释放
                # 重新加载资源后重试reserve
                if seconds_to_release <= 0:
                    # 重新加载最新的资源状态
                    self.env._reload_resources()
                    wait_attempted = True
                    continue

                # 轮询等待释放
                wait_attempted = True

                # 如果seconds_to_release < 0，说明释放时间超过max_wait_seconds
                # 最多等待max(1s, poll_interval)然后再试
                if seconds_to_release < 0:
                    # 释放时间太远，等待较短时间后重试
                    sleep_seconds = max(1.0, self.lease_manager.poll_interval)
                else:
                    # 在等待预算内，释放应该很快发生
                    sleep_seconds = min(
                        max(seconds_to_release, self.lease_manager.poll_interval),
                        remaining_wait,
                    )
                if sleep_seconds <= 0:
                    break

                # 等待后释放到期的资源
                logger.info(f"[{task_id}] waiting {sleep_seconds}s for resource release")
                time.sleep(sleep_seconds)
                resource_waited_seconds = time.monotonic() - wait_start
                self.lease_manager.release_due_allocations()

            # 资源预留失败
            if not reserved:
                fail_result = {
                    "status": "failed",
                    "task_id": task_id,
                    "data_id": data_id,
                    "anomaly_score": message.get("anomaly_score", 0.5),
                    "risk_level": message.get("risk_level", "NORMAL"),
                    "error": reservation.get("error", "resource_reserve_failed"),
                    "resource_ok": False,
                    "resource_need": node_demands,
                    "resource_detail": reservation,
                    "resource_wait_seconds": round(resource_waited_seconds, 3),
                    "resource_wait_budget": self.lease_manager.wait_seconds,
                }
                logger.warning(f"[{task_id}] reserve failed: {reservation}")
                return fail_result

            allocation_id = reservation.get("allocation_id")

            # 执行编排
            obs, reward, terminated, truncated, info = self.env.step(action_dict)

            # 获取链路分配信息
            link_path = info.get("link_path", {})

            # 更新数据库中的link_path
            if allocation_id and link_path:
                try:
                    import pymysql
                    conn = get_mysql_connection(cursor_class=pymysql.cursors.DictCursor)
                    with conn.cursor() as c:
                        c.execute("""
                            UPDATE t_service_allocation
                            SET link_path = %s
                            WHERE allocation_id = %s
                        """, (json.dumps(link_path, ensure_ascii=False), allocation_id))
                        conn.commit()
                    logger.info(f"[{task_id}] link_path updated for allocation_id={allocation_id}")
                except Exception as e:
                    logger.warning(f"[{task_id}] link_path update failed: {e}")
                finally:
                    conn.close()

            decision_plan = {
                "vnf_node": [_normalize_node_id(int(n)) for n in action_dict["vnf_node"]],
                "qos_vector": qos_vector,
            }
            risk_snapshot = {
                "anomaly_score": message.get("anomaly_score", 0),
                "risk_level": message.get("risk_level", "NORMAL"),
            }
            log_id = write_orchestration_log(data_id, risk_snapshot, decision_plan, float(reward))
            logger.info(f"[{task_id}] orchestration log inserted: log_id={log_id}")

            # 返回结果（含资源租约信息）
            result = {
                "status": "success",
                "task_id": task_id,
                "data_id": data_id,
                "log_id": log_id,
                "anomaly_score": message.get("anomaly_score", 0),
                "risk_level": message.get("risk_level", "NORMAL"),
                "qos_vector": qos_vector,
                "vnf_node": [_normalize_node_id(int(n)) for n in action_dict["vnf_node"]],
                "link_path": link_path,
                "reward": round(float(reward), 2),
                "qos_ok": bool(info.get("qos_ok")),
                "resource_ok": True,
                "env_resource_ok": bool(info.get("resource_ok")),
                "resource_plan": resource_req,
                "allocation_id": reservation.get("allocation_id"),
                "node_allocation": reservation.get("node_allocation", {}),
                "resource_hold_seconds": self.lease_manager.hold_seconds,
                "resource_waited_seconds": round(resource_waited_seconds, 3),
                "resource_wait_enabled": wait_attempted,
            }

            # 更新链路分配到数据库（使用 t_physical_link 表）
            if allocation_id and link_path:
                try:
                    conn = get_mysql_connection()
                    with conn.cursor() as c:
                        logger.info(f"[{task_id}] Starting link update, allocation_id={allocation_id}, link_path={link_path}")
                        c.execute("""
                            UPDATE t_service_allocation
                            SET link_path = %s
                            WHERE allocation_id = %s
                        """, (json.dumps(link_path, ensure_ascii=False), allocation_id))

                        # 更新链路资源的已用带宽 - UPDATE t_physical_link 表
                        for link_idx, link_info in link_path.items():
                            link_id = link_info.get("link_id")
                            bw_demand = link_info.get("bandwidth", 0.1)
                            src = link_info.get("src_node") or link_info.get("src")
                            dst = link_info.get("dst_node") or link_info.get("dst")

                            # 如果 src/dst 是数字索引，转换为节点名称
                            src_node = None
                            dst_node = None

                            if src is not None and dst is not None:
                                if isinstance(src, int) and isinstance(dst, int):
                                    # 索引到节点名称映射 (edge-1=0, edge-2=1, edge-3=2, cloud-1=3, cloud-2=4)
                                    index_to_node = {0: 'edge-1', 1: 'edge-2', 2: 'edge-3', 3: 'cloud-1', 4: 'cloud-2'}
                                    src_node = index_to_node.get(src)
                                    dst_node = index_to_node.get(dst)
                                elif isinstance(src, str) and isinstance(dst, str):
                                    src_node = src
                                    dst_node = dst

                            # 如果 link_id 为 null，通过 src_node 和 dst_node 查找
                            if link_id is None and src_node is not None and dst_node is not None:
                                logger.info(f"[{task_id}] Looking for link: src={src_node}, dst={dst_node}")
                                c.execute("""
                                    SELECT link_id FROM t_physical_link
                                    WHERE (src_node = %s AND dst_node = %s) OR (src_node = %s AND dst_node = %s) AND is_active = 1
                                """, (src_node, dst_node, dst_node, src_node))
                                row = c.fetchone()
                                if row:
                                    link_id = row['link_id']
                                    logger.info(f"[{task_id}] Found link_id: {link_id}")
                                else:
                                    logger.warning(f"[{task_id}] Link not found for src={src_node}, dst={dst_node}")

                            if link_id is not None:
                                # 查询当前 used_bandwidth_mbps
                                c.execute("""
                                    SELECT used_bandwidth_mbps, allocations
                                    FROM t_physical_link
                                    WHERE link_id = %s AND is_active = 1
                                    FOR UPDATE
                                """, (link_id,))
                                row = c.fetchone()
                                if row:
                                    current_used_bandwidth_mbps = _to_float(row.get("used_bandwidth_mbps", 0.0), 0.0)
                                    new_used_bandwidth_mbps = round(current_used_bandwidth_mbps + bw_demand, 4)
                                    new_allocations = int(row.get("allocations", 0) or 0) + 1
                                    # UPDATE t_physical_link 列
                                    c.execute("""
                                        UPDATE t_physical_link
                                        SET used_bandwidth_mbps = %s, allocations = %s
                                        WHERE link_id = %s AND is_active = 1
                                    """, (new_used_bandwidth_mbps, new_allocations, link_id))
                                    logger.info(f"link [{link_id}] allocated: used_bandwidth_mbps={new_used_bandwidth_mbps}, allocations={new_allocations}")

                        conn.commit()
                    conn.close()
                except Exception as e:
                    logger.warning(f"[{task_id}] update link_path failed: {e}")
                finally:
                    pass

            # 资源已预分配，返回成功结果
            result["release_time"] = reservation.get("release_time")  # 已由INSERT设置
            logger.info(f"[{task_id}] result published, allocation_id={allocation_id}")
            return result

        except Exception as e:
            logger.error(f"[{task_id}] process failed: {e}", exc_info=True)
            return {
                "status": "error",
                "task_id": task_id,
                "error": str(e),
            }

    def check_resources(self, qos_vector: list, resource_req: dict, vnf_count: int = 2) -> dict:
        """预检查资源是否充足

        Returns:
            {"status": "sufficient", "available": {...}}
            {"status": "waiting", "release_info": {...}}
            {"status": "insufficient", "suggestions": [...], "required": {...}, "available": {...}}
        """
        import numpy as np

        # 1. 构建虚拟需求
        self.env.vnf_count = vnf_count
        self.env._reload_resources()
        qos_arr = np.array(qos_vector, dtype=np.float32)
        obs, _ = self.env.reset()
        self.env.set_qos_to_vnr(qos_arr)
        obs = self.env._get_obs()
        action_dict = self.ppo_agent.predict(obs)

        # 2. 构建节点需求
        node_demands = self.lease_manager.build_node_demands(action_dict, resource_req)
        resource_ids = list(node_demands.keys())

        # 3. 查询当前可用资源
        available = self.lease_manager.get_available_resources()

        # 4. 检查是否充足
        insufficient_nodes = []
        for node_id in resource_ids:
            if node_id not in available:
                insufficient_nodes.append(node_id)
                continue
            node_avail = available[node_id]
            demand = node_demands.get(node_id, {})
            if node_avail["free_vcpu"] < demand.get("vcpu", 0) or node_avail["free_memory_gb"] < demand.get("memory_gb", 0):
                insufficient_nodes.append(node_id)

        # 5. 如果有不足，检查5秒释放窗口
        if insufficient_nodes:
            seconds_to_release = self.lease_manager.get_seconds_until_candidate_release(
                resource_ids, 5
            )

            if seconds_to_release is not None:
                # 有资源即将释放
                return {
                    "status": "waiting",
                    "message": f"有资源将在{int(seconds_to_release)}秒后释放，是否愿意等待？",
                    "release_info": {
                        "seconds_left": round(seconds_to_release, 1),
                        "resource_ids": resource_ids,
                    },
                    "options": ["wait", "cancel"],
                }

            # 无释放窗口，返回不足
            required = {"vcpu": sum(d.get("vcpu", 0) for d in node_demands.values()),
                      "memory_gb": sum(d.get("memory_gb", 0) for d in node_demands.values())}
            avail_summary = {"vcpu": sum(a["free_vcpu"] for a in available.values()),
                         "memory_gb": sum(a["free_memory_gb"] for a in available.values())}

            return {
                "status": "insufficient",
                "message": "资源不足，无法满足当前编排需求",
                "error": "resource_insufficient",
                "suggestions": ["添加节点资源", "等待当前任务完成", "撤回编排"],
                "required": required,
                "available": avail_summary,
            }

        # 资源充足
        return {
            "status": "sufficient",
            "message": "资源充足，可以编排",
            "available": {k: {"free_vcpu": v["free_vcpu"], "free_memory_gb": v["free_memory_gb"]}
                           for k, v in available.items()},
        }

    def get_resource_status(self) -> dict:
        """查看资源剩余量API"""
        return self.lease_manager.get_available_resources()

    def run(self):
        """运行服务 - 消费队列"""
        logger.info("orchestration service started, waiting for messages")
        while True:
            try:
                # 释放过期资源
                self.lease_manager.release_due_allocations()

                # 尝试消费编排队列
                from common.message_queue import MessageQueue, get_message_queue
                mq = get_message_queue()
                message = mq.consume(MessageQueue.QUEUE_ORCHESTRATION, blocking=True, timeout=1)

                if message:
                    logger.info(f"收到编排请求: task_id={message.get('task_id')}")
                    result = self.process(message)
                    mq.push_result(message.get('task_id'), result)
                    logger.info(f"编排完成: status={result.get('status')}")

            except KeyboardInterrupt:
                logger.info("received interrupt, exit")
                break
            except Exception as e:
                logger.error(f"service loop error: {e}")


def _get_worker_count(env_name: str, default: int) -> int:
    """获取worker数量"""
    raw = os.environ.get(env_name, str(default))
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def main():
    """主入口"""
    logger.info("=" * 30)
    logger.info("start orchestration microservice (整合版)")
    logger.info("=" * 30)

    worker_count = _get_worker_count("ORCHESTRATION_WORKERS", 1)
    if worker_count == 1:
        OrchestrationService().run()
        return

    # 多进程模式
    logger.info(f"multi-process enabled, workers={worker_count}")
    ctx = mp.get_context("spawn")
    workers = []
    for worker_id in range(1, worker_count + 1):
        p = ctx.Process(
            target=lambda: OrchestrationService().run(),
            name=f"orchestration-worker-{worker_id}",
        )
        p.start()
        workers.append(p)

    try:
        for p in workers:
            p.join()
    except KeyboardInterrupt:
        logger.info("received interrupt, stopping all workers")
        for p in workers:
            if p.is_alive():
                p.terminate()
        for p in workers:
            p.join()


if __name__ == "__main__":
    main()