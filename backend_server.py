#!/usr/bin/env python3
"""
用户中心 HTTP 服务器
只处理用户登录/注册，不处理其他业务逻辑
端口：8003
"""
import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pymysql
import pymysql.cursors

PORT = 8003

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# 数据库配置
_MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
_MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
_MYSQL_USER = os.environ.get("MYSQL_USER", "qos_app")
_MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "QosApp@123")

DB_USER = {
    "host": _MYSQL_HOST,
    "port": _MYSQL_PORT,
    "user": _MYSQL_USER,
    "password": _MYSQL_PASSWORD,
    "database": "qos_user_center",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor
}


# ========== 认证 ==========

def do_login(username: str, password: str) -> dict:
    conn = pymysql.connect(**DB_USER)
    with conn.cursor() as c:
        c.execute("SELECT user_id, username, role FROM t_user WHERE username=%s AND password_hash=%s", (username, password))
        row = c.fetchone()
    conn.close()

    if not row:
        return {"result": "fail", "message": "用户名或密码错误"}

    return {
        "result": "ok",
        "user_id": row["user_id"],
        "username": row["username"],
        "role": row["role"],
        "token": f"token-{row['user_id']}-{row['role']}"
    }


def do_register(username: str, password_hash: str) -> dict:
    try:
        conn = pymysql.connect(**DB_USER)
        with conn.cursor() as c:
            c.execute("INSERT INTO t_user (username, password_hash, role) VALUES (%s, %s, %s)", (username, password_hash, "user"))
            conn.commit()
        conn.close()
        return {"result": "ok"}
    except Exception as e:
        code = e.args[0] if e.args else 0
        if code == 1062:
            return {"result": "fail", "message": "用户名已存在"}
        raise


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
            # 兼容前端发送的 email/name/username 字段
            username = data.get("username") or data.get("email") or data.get("name") or ""
            password = data.get("password", "")
            result = do_login(username, password)
            self._send_json(result, 200 if result.get("result") == "ok" else 400)
            return

        # ===== 注册 =====
        if path == "/register":
            # 兼容前端发送的 email/name/username 字段
            username = data.get("username") or data.get("email") or data.get("name") or ""
            password = data.get("password", "")
            result = do_register(username, password)
            self._send_json(result, 200 if result.get("result") == "ok" else 400)
            return

        self._send_json({"error": "unknown endpoint"}, 404)


# ========== 启动 ==========

if __name__ == "__main__":
    print("=" * 50)
    print("启动用户中心服务: http://localhost:8003")
    print("功能：用户登录/注册")
    print("=" * 50)

    # 切换工作目录到 frontend 以便提供静态文件
    fe_dir = os.path.join(BASE_DIR, "frontend")
    if os.path.exists(fe_dir):
        os.chdir(fe_dir)

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print("用户中心服务运行于 http://localhost:8003")
    server.serve_forever()