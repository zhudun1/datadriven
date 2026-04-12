# 数据驱动编排系统 - 服务启动步骤

## 环境要求

- **MySQL**: 127.0.0.1:3306 (已存在)
  - 用户: `qos_app` / 密码: `QosApp@123`
  - 初始化脚本: `docker/mysql/init.sql`

- **Redis**: 127.0.0.1:6379 (已存在)
  - 密码: `123456`

## 启动步骤

### 1. 初始化数据库

```bash
# 执行初始化脚本，创建数据库和表
mysql -h 127.0.0.1 -u root -pQosRoot@123 < docker/mysql/init.sql
```

### 2. 启动后端微服务

在 `D:\data-driven_2` 目录下分别启动三个服务：

```bash
# API 网关 (端口 8001)
python -m services.gateway

# 业务感知服务 (后台运行)
python -m services.perception_service

# 智能编排服务 (后台运行)
python -m services.orchestration_service
```

### 3. 启动前端服务

```bash
# 前端界面 (端口 8003)
python server.py
```

## 快速启动脚本 (start_services.bat)

```batch
@echo off
cd /d D:\data-driven_2

echo 启动后端微服务...
start "Gateway" python -m services.gateway
start "Perception" python -m services.perception_service
start "Orchestration" python -m services.orchestration_service

echo 启动前端服务...
start "Frontend" python server.py

echo 所有服务已启动！
echo Gateway: http://localhost:8001
echo Frontend: http://localhost:8003
pause
```

## 验证服务状态

```bash
# 检查前端
curl http://localhost:8003/health

# 检查网关
curl http://localhost:8001/health
```

## 服务架构

```
浏览器 → Frontend (8003) → Gateway (8001)
                                ↓
                          消息队列 (Redis)
                                ↓
                    ┌───────────┴───────────┐
                    ↓                       ↓
            感知服务                编排服务
                    ↓                       ↓
                  MySQL                  MySQL
```

## 注意事项

- 启动顺序：先启动感知和编排服务，再启动 Gateway 和 Frontend
- 所有服务默认使用本地 Redis 和 MySQL
- 如需修改配置，可通过环境变量覆盖:
  - `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`
  - `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`