# 数据编排系统服务启动流程

## 概述

本文档说明如何启动所有后端服务和前端界面。

## 服务端口

| 服务 | 端口 | 文件 | 功能 |
|------|------|------|------|
| 用户中心 | 8003 | backend_server.py | 登录/注册 + 前端静态文件 |
| Gateway | 8001 | services/gateway.py | 资源管理、编排请求入口 |

## 启动命令

在 `D:\data-driven_2` 目录下分别启动两个服务（三个终端）：

```bash
# 终端1: API 网关 (端口 8001) - 方式1: 直接运行
cd D:/data-driven_2
python services/gateway.py

# 或者终端1: API 网关 (端口 8001) - 方式2: uvicorn
cd D:/data-driven_2
python -m uvicorn services.gateway:app --host 127.0.0.1 --port 8001

# 终端2: 用户中心 (端口 8003)
cd D:/data-driven_2
python backend_server.py


```

**备注**: 编排服务和感知服务通过 Gateway API 内部调用，无需单独启动（已集成到 gateway.py）

## API接口

| 接口 | 方法 | 功能 |
|------|------|------|
| http://127.0.0.1:8003/login | POST | 用户登录 |
| http://127.0.0.1:8003/register | POST | 用户注册 |
| http://127.0.0.1:8001/health | GET | 健康检查 |
| http://127.0.0.1:8001/api/resources | GET | 获取计算节点 |
| http://127.0.0.1:8001/api/links | GET | 获取网络链路 |
| http://127.0.0.1:8001/api/history | GET | 编排历史 |
| http://127.0.0.1:8001/api/orchestrate | POST | 提交编排 |
| http://127.0.0.1:8001/api/release | POST | 释放资源 |
| http://127.0.0.1:8001/resources/nodes | POST | 添加节点 |
| http://127.0.0.1:8001/resources/links | POST | 添加链路 |

## 测试账号

- 用户名: admin
- 密码: admin123

## 访问界面

- 登录页面: http://localhost:8003/
- 工作台: http://localhost:8003/sandbox/

## 代码文件结构

### 核心服务
- `services/gateway.py` - FastAPI网关，处理所有REST API请求
- `services/perception_service.py` - 业务感知服务（异常检测）
- `intelligent_orchestration/orchestration_service.py` - 编排服务（资源分配）
- `common/message_queue.py` - Redis消息队列封装

### 前端页面 (frontend/sandbox/)
- `index.html` - 登录页面
- `step2-data-type.html` - 数据类型选择
- `step3-file-input.html` - 文件上传
- `step4-model-confirm.html` - 模型确认
- `step5-result.html` - 结果展示
- `step6-orchestration-visualization.html` - 资源管理（显示节点、链路、历史）

### 配置文件
- `main.py` - 主入口（未使用）
- `db_config.py` - 数据库配置
- `backend_server.py` - 用户中心服务

## 测试模型

### CFM 模型（图像 + 点云异常检测）

在 sandbox 界面：
1. 选择模型：**CFM (图像 + 点云)**
2. 输入路径：
   - RGB图像路径: `datasets/mvtec3d/cable_gland/train/good/rgb/000.png`
   - 点云路径: `datasets/mvtec3d/cable_gland/train/good/xyz/000.tiff`
3. 点击「提交任务」

预期结果：anomaly_score ≈ 0.6x, risk_level = NORMAL

### Jigsaw 模型（视频异常检测）

在 sandbox 界面：
1. 选择模型：**Jigsaw (视频 + 图像)**
2. 输入路径：
   - 视频路径: `Jigsaw-VAD-main/Avenue Dataset/testing_videos/01.avi`
3. 点击「提交任务」

预期结果：anomaly_score ≈ 0.9x, risk_level = CRITICAL

## 验证服务状态

```bash
# 检查服务健康状态
curl http://127.0.0.1:8001/health    # API网关
curl http://127.0.0.1:8003/health    # 用户中心

# 验证所有API
curl http://127.0.0.1:8001/api/resources    # 获取节点
curl http://127.0.0.1:8001/api/links       # 获取链路
curl http://127.0.0.1:8001/api/history   # 获取历史
```

## 关键修复说明

### 资源等待超时问题 (resource_wait_timeout)

**问题现象**：
编排请求返回 `resource_wait_timeout` 错误，提示节点资源不足。

**原因**：
1. 之前的 active allocation 没有被自动释放
2. 系统没有在编排前清理过期资源的逻辑

**解决方案**：
在 `gateway.py` 的 `orchestrate_simple` 函数中添加编排前自动清理：
```python
service.lease_manager.release_due_allocations()
```

系统现在会自动等待资源释放后再编排，无需手动调用释放接口。

### 问题现象
CFM 模型调用后总是返回 0.5，感知服务日志显示已正确检测到 0.6+ 的分数。

### 根因（第三次出现）
1. **旧进程未杀死** - 每次修改代码后必须完全杀死所有 Python 进程再重启
2. **Duplicate entry 错误时** - `orchestration_service.py` 的 `fail_result` 没有包含 `anomaly_score` 字段

### 解决方案
1. 修改后杀掉所有 Python 进程：`taskkill //F //IM python.exe`
2. 修复 `orchestration_service.py` 542-554行，添加 `anomaly_score` 到 `fail_result`
3. 修复 `gateway.py` 629-686行，添加 `rgb_path`, `pcd_path` 参数

1. **step6 页面增强**
   - 添加链路可视化 (`/api/links`)
   - 修复历史记录API (使用正确的数据库字段)
   - 添加资源对话框（添加节点/链路）

2. **sys.path 顺序问题**

CFM 和 Jigsaw 模型存在 sys.path 冲突：
- CFM 使用 `D:\data-driven_2\models\`
- Jigsaw 使用 `D:\data-driven_2\Jigsaw-VAD-main\models\`

**解决方案**：
1. 在 `jigsaw_detector.py` 中使用 `importlib.util` 动态加载模型文件
2. 在加载前清理 `sys.modules` 中的缓存
3. 正确设置 sys.path 顺序

3. **消息队列修复**

Blpop 超时问题：
- 修改 `common/message_queue.py` 使用 lpop 轮询
- 添加 Redis 连接池配置

