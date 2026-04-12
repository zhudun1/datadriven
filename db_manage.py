
import json
from datetime import datetime
from typing import Optional
import pymysql
import pymysql.cursors

# [DB_ADAPT] 兼容两种调用方式：
# 1) 作为包导入 common.db_manage（推荐）
# 2) 在 common 目录下直接执行脚本（历史方式）
try:
    from .db_config import DB_CONFIGS
except Exception:
    from db_config import DB_CONFIGS


def _get_conn(db_key: str) -> pymysql.connections.Connection:
    """建立并返回一个数据库连接。"""
    # 通过逻辑库标识路由到对应数据库，适配一套代码管理多库。
    if db_key not in DB_CONFIGS:
        raise ValueError(f"Unknown database key: {db_key}")
    config = {**DB_CONFIGS[db_key], "cursorclass": pymysql.cursors.DictCursor}
    return pymysql.connect(**config)



# t_industrial_data — 工业多模态数据


def insert_data(rgb_path: Optional[str], pcd_path: Optional[str]) -> int:
    """
    写入一条新的工业帧数据。
    至少提供 rgb_path / pcd_path 其中之一（与表的 CHECK 约束一致）。
    返回新记录的 data_id。
    """
    if rgb_path is None and pcd_path is None:
        raise ValueError("rgb_path 与 pcd_path 不能同时为 None")

    sql = """
        INSERT INTO t_industrial_data (rgb_path, pcd_path)
        VALUES (%s, %s)
    """
    with _get_conn("business_awareness") as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (rgb_path, pcd_path))
            conn.commit()
            return cur.lastrowid


def list_unprocessed() -> list[dict]:
    """
    查询所有尚未处理的数据（is_processed = 0）。
    返回字典列表，每项对应一行记录。
    """
    sql = "SELECT * FROM t_industrial_data WHERE is_processed = 0"
    with _get_conn("business_awareness") as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


def mark_as_processed(data_id: int) -> None:
    """将指定记录标记为已处理（is_processed = 1）。"""
    sql = "UPDATE t_industrial_data SET is_processed = 1 WHERE data_id = %s"
    with _get_conn("business_awareness") as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (data_id,))
            conn.commit()



# t_mapping_rules — QoS 映射规则

def find_rule(risk_level: str, sensitivity: str) -> Optional[dict]:
    """
    按风险等级与敏感类型查询优先级最高的 QoS 映射规则。
    risk_level 取值: 'NORMAL' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
    sensitivity 取值: 'LATENCY_SENSITIVE' | 'BANDWIDTH_SENSITIVE' | 'JITTER_SENSITIVE' | 'RELIABILITY_SENSITIVE'
    返回单条规则字典，未匹配时返回 None。
    """
    sql = """
        SELECT * FROM t_mapping_rules
        WHERE risk_level = %s AND sensitivity = %s
        ORDER BY priority DESC
        LIMIT 1
    """
    with _get_conn("business_awareness") as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (risk_level, sensitivity))
            return cur.fetchone()



# t_orchestration_log — 编排决策日志

def insert_log(
    data_id: Optional[int],
    risk_snapshot: dict,
    decision_plan: dict,
    expected_reward: float,
) -> int:
    """
    写入一条 PPO 编排决策记录。
    data_id 可为 None（表示仅记录编排决策，不绑定具体输入数据）。
    risk_snapshot / decision_plan 传入 Python dict，自动序列化为 JSON。
    返回新记录的 log_id。
    """
    sql = """
        INSERT INTO t_orchestration_log
            (data_id, risk_snapshot, decision_plan, expected_reward)
        VALUES (%s, %s, %s, %s)
    """
    with _get_conn("intelligent_orchestration") as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                data_id,
                json.dumps(risk_snapshot, ensure_ascii=False),
                json.dumps(decision_plan, ensure_ascii=False),
                expected_reward,
            ))
            conn.commit()
            return cur.lastrowid


def list_logs_by_data_id(data_id: int) -> list[dict]:
    """
    查询某条工业数据对应的所有编排决策历史，按生成时间升序排列。
    """
    sql = """
        SELECT * FROM t_orchestration_log
        WHERE data_id = %s
        ORDER BY create_time ASC
    """
    with _get_conn("intelligent_orchestration") as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (data_id,))
            return cur.fetchall()


# t_resource_inventory — 网络与计算资源清单
def list_active_resources() -> list[dict]:
    """
    查询所有逻辑启用的资源（节点 + 链路），供 PPO 算法读取当前状态。
    """
    sql = "SELECT * FROM t_resource_inventory WHERE is_active = 1"
    with _get_conn("intelligent_orchestration") as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


# [DB_ADAPT] 按资源ID查询单条资源记录（用于状态补丁式更新）。
def get_resource_by_id(resource_id: str) -> Optional[dict]:
    sql = "SELECT * FROM t_resource_inventory WHERE resource_id = %s LIMIT 1"
    with _get_conn("intelligent_orchestration") as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (resource_id,))
            return cur.fetchone()


def update_resource_state(resource_id: str, current_state: dict) -> None:
    """
    更新指定资源的动态状态（current_state 字段）。
    current_state 传入 Python dict，自动序列化为 JSON。
    """
    sql = """
        UPDATE t_resource_inventory
        SET current_state = %s
        WHERE resource_id = %s
    """
    with _get_conn("intelligent_orchestration") as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                json.dumps(current_state, ensure_ascii=False),
                resource_id,
            ))
            conn.commit()


# [DB_ADAPT] 补丁式更新资源状态：保留现有字段，仅覆盖传入字段。
def patch_resource_state(resource_id: str, state_patch: dict) -> None:
    row = get_resource_by_id(resource_id)
    current_state = {}
    if row:
        raw_state = row.get("current_state")
        if isinstance(raw_state, dict):
            current_state = raw_state
        elif isinstance(raw_state, str):
            try:
                current_state = json.loads(raw_state)
            except json.JSONDecodeError:
                current_state = {}

    merged = {**current_state, **state_patch}
    update_resource_state(resource_id, merged)



# t_alarms — 系统告警


def insert_alarm(level: str, content: str, source_module: str) -> int:
    """
    写入一条告警记录。
    level 取值: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
    返回新记录的 alarm_id。
    """
    sql = """
        INSERT INTO t_alarms (`level`, content, source_module)
        VALUES (%s, %s, %s)
    """
    with _get_conn("intelligent_orchestration") as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (level, content, source_module))
            conn.commit()
            return cur.lastrowid


def list_unread_alarms() -> list[dict]:
    """查询所有未被管理员查看的告警，按 alarm_id 升序。"""
    sql = """
        SELECT * FROM t_alarms
        WHERE is_read = 0
        ORDER BY alarm_id ASC
    """
    with _get_conn("intelligent_orchestration") as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


def mark_alarm_as_read(alarm_id: int) -> None:
    """将指定告警标记为已读（is_read = 1）。"""
    sql = "UPDATE t_alarms SET is_read = 1 WHERE alarm_id = %s"
    with _get_conn("intelligent_orchestration") as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (alarm_id,))
            conn.commit()


# t_user — 用户与权限


def find_user_by_username(username: str) -> Optional[dict]:
    """
    按用户名查询用户信息（含 password_hash、role）。
    用于登录验证，未找到时返回 None。
    """
    sql = "SELECT * FROM t_user WHERE username = %s LIMIT 1"
    with _get_conn("user_center") as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (username,))
            return cur.fetchone()


def insert_user(username: str, password_hash: str, role: str) -> bool:
    """
    新增用户记录。
    返回 True 表示成功；若 username 冲突(1062)返回 False。
    """
    sql = "INSERT INTO t_user (username, password_hash, role) VALUES (%s, %s, %s)"
    with _get_conn("user_center") as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(sql, (username, password_hash, role))
                conn.commit()
                return True
            except Exception as exc:
                code = getattr(exc, "args", [None])[0]
                if code == 1062:
                    return False
                raise


def ensure_user(username: str, password_hash: str, role: str) -> None:
    """
    确保指定用户存在：
    - 已存在：不改动
    - 不存在：创建
    """
    if find_user_by_username(username) is not None:
        return
    insert_user(username, password_hash, role)


def update_last_login(user_id: int) -> None:
    """登录成功后更新用户的最后登录时间为当前时间。"""
    sql = "UPDATE t_user SET last_login = %s WHERE user_id = %s"
    with _get_conn("user_center") as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (datetime.now(), user_id))
            conn.commit()
