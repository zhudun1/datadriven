"""
Redis消息队列封装模块
用于微服务间通信
支持模拟模式（无Redis时使用内存队列）
"""
import json
import uuid
import os
import time
from typing import Any, Optional
from collections import deque
from common.utils import init_logger

logger = init_logger()

# 尝试导入redis
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis未安装，使用模拟模式（内存队列）")


class MessageQueue:
    """Redis消息队列封装类"""

    # 队列常量（使用下划线避免 Redis 兼容性问题）
    QUEUE_PERCEPTION = "rq_perception"
    QUEUE_ORCHESTRATION = "rq_orchestration"
    QUEUE_INFRA = "rq_infra"
    QUEUE_RESULTS = "rq_results"
    QUEUE_RESOURCE_OPS = "rq_resource_ops"  # 资源操作队列（增/删节点链路）

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0, password: str = None):
        self.host = host
        self.port = port
        self.db = db
        self._mock_mode = False

        if REDIS_AVAILABLE:
            try:
                self.redis_client = redis.Redis(
                    host=host,
                    port=port,
                    db=db,
                    password=password,
                    decode_responses=True,
                    socket_connect_timeout=10,
                    socket_timeout=None  # 无限等待
                )
                self.redis_client.ping()
                logger.info(f"Redis连接成功: {host}:{port}/{db}")
            except Exception as e:
                logger.warning(f"Redis连接失败: {e}，切换到模拟模式")
                self._mock_mode = True
                self.redis_client = MockMessageQueue()
        else:
            self._mock_mode = True
            self.redis_client = MockMessageQueue()
            logger.info("使用内存模拟队列")

    def _serialize(self, data: dict) -> str:
        return json.dumps(data, ensure_ascii=False)

    def _deserialize(self, data: str) -> dict:
        return json.loads(data)

    def publish(self, queue_name: str, message: dict) -> str:
        if "task_id" not in message:
            message["task_id"] = str(uuid.uuid4())[:8]

        self.redis_client.rpush(queue_name, self._serialize(message))
        logger.info(f"消息已发布到 {queue_name}: task_id={message['task_id']}")
        return message["task_id"]

    def consume(self, queue_name: str, blocking: bool = True, timeout: int = 0) -> Optional[dict]:
        if blocking:
            result = self.redis_client.blpop(queue_name, timeout=timeout)
            if result is None:
                return None
            _, data = result
        else:
            data = self.redis_client.lpop(queue_name)
            if data is None:
                return None

        message = self._deserialize(data)
        logger.info(f"消费消息 from {queue_name}: task_id={message.get('task_id')}")
        return message

    def push_result(self, task_id: str, result: dict) -> None:
        message = {
            "task_id": task_id,
            "status": "completed",
            "result": result
        }
        self.publish(self.QUEUE_RESULTS, message)

    def get_result(self, task_id: str, timeout: int = 60) -> Optional[dict]:
        start_time = time.time()
        while time.time() - start_time < timeout:
            for i in range(self.redis_client.llen(self.QUEUE_RESULTS)):
                data = self.redis_client.lindex(self.QUEUE_RESULTS, i)
                if data:
                    msg = self._deserialize(data)
                    if msg.get("task_id") == task_id:
                        self.redis_client.lrem(self.QUEUE_RESULTS, 1, data)
                        return msg.get("result")
            time.sleep(0.5)

        logger.warning(f"等待结果超时: task_id={task_id}")
        return None

    def close(self) -> None:
        self.redis_client.close()
        logger.info("消息队列连接已关闭")


class MockMessageQueue:
    """内存模拟队列（当Redis不可用时）"""

    def __init__(self):
        self.queues = {
            "rq:perception": deque(),
            "rq:orchestration": deque(),
            "rq:infra": deque(),
            "rq:results": deque(),
            "rq:resource_ops": deque()
        }

    def rpush(self, queue_name: str, message: str):
        self.queues[queue_name].append(message)

    def blpop(self, queue_name: str, timeout: int = 0):
        if self.queues[queue_name]:
            return (queue_name, self.queues[queue_name].popleft())
        if timeout > 0:
            time.sleep(timeout)
        return None

    def lpop(self, queue_name: str):
        if self.queues[queue_name]:
            return self.queues[queue_name].popleft()
        return None

    def lrem(self, queue_name: str, count: int, value: str):
        queue = self.queues[queue_name]
        new_queue = deque([x for x in queue if x != value])
        self.queues[queue_name] = new_queue
        return count

    def lindex(self, queue_name: str, index: int):
        if 0 <= index < len(self.queues[queue_name]):
            return self.queues[queue_name][index]
        return None

    def llen(self, queue_name: str):
        return len(self.queues[queue_name])

    def close(self):
        pass


# 全局单例
_mq_instance: Optional[MessageQueue] = None


def get_message_queue() -> MessageQueue:
    """获取全局消息队列实例"""
    global _mq_instance
    if _mq_instance is None:
        redis_host = os.environ.get("REDIS_HOST", "127.0.0.1")
        redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        redis_db = int(os.environ.get("REDIS_DB", "0"))
        redis_password = os.environ.get("REDIS_PASSWORD") or "123456"  # 默认密码
        _mq_instance = MessageQueue(host=redis_host, port=redis_port, db=redis_db, password=redis_password)
    return _mq_instance