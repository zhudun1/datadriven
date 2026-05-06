# 数据驱动智能编排系统

基于异常检测的QoS智能编排系统，支持CFM（点云+图像）和Jigsaw（视频）两种异常检测模型，实现智能资源编排。

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      前端 (Port 8003)                       │
│  step1: 选择模式 → step2: 数据类型 → step3: 文件输入          │
│  → step4: 模型确认 → step5: 结果展示 → step6: 编排可视化     │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                  API网关 (Port 8001)                         │
│  /api/orchestrate - 编排入口                               │
│  /api/thresholds - 异常阈值API                           │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  感知服务    │  │  编排服务    │  │  用户中心    │
│  异常检测   │  │  PPO智能决策 │  │  登录注册   │
└──────────────┘  └──────────────┘  └──────────────┘
```

## 环境要求

- Python 3.10+
- MySQL 8.0
- Redis (可选，用于消息队列)
- PyTorch (CPU模式)
- CUDA 11.7 (GPU支持，可选)

## 目录结构

```
data-driven_2/
├── business_perception/     # 业务感知服务 (异常检测)
│   ├── cfm_detector.py    # CFM模型检测器
│   └── jigsaw_detector.py # Jigsaw模型检测器
├── checkpoints/           # ⭐ 模型权重文件
├── common/               # 公共组件
├── datasets/             # ⭐ 训练/测试数据集
├── docs/                # 设计文档
├── frontend/             # 前端页面
│   └── sandbox/        # 工作台界面
├── intelligent_orchestration/  # 智能编排
│   ├── ppo_agent.py  # PPO决策智能体
│   ├── vne_env.py   # VNE环境
│   └── orchestration_service.py
├── Jigsaw-VAD-main/     # ⭐ Jigsaw模型代码
├── models/             # ⭐ CFM模型权重
├── crossmodal-feature-mapping/  # ⭐ CFM特征提取代码
├── Pointnet2_PyTorch-master/   # ⭐ PointNet2操作库
├── services/           # 后端服务
│   ├── gateway.py    # API网关
│   └── perception_service.py
├── START_SERVICES.md  # 服务启动说明
└── qos映射.md       # QoS映射规则
```

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/zhudun1/datadriven.git
cd datadriven
```

### 2. 安装依赖

```bash
pip install torch torchvision
pip install stable-baselines3 gymnasium
pip install pymysql redis fastapi uvicorn
pip install numpy pandas scikit-learn
```

### 3. 配置数据库

```bash
# 创建数据库
mysql -u root -p
CREATE DATABASE intelligent_orchestration;
CREATE DATABASE qos_user_center;
```

### 4. 下载必要文���

系统需要以下文件才能运行，请按下方说明放置：

#### 4.1 CFM模型权重

下载链接：https://drive.google.com/file/d/1X2yZ9xXxX/view

```
放入目录: models/
文件: feature_extractors/dino_vitbase8_pretrain.pth
```

#### 4.2 Jigsaw模型权重

下载链接：https://github.com/jnian1992/Jigsaw-VAD/releases

```
放入目录: Jigsaw-VAD-main/checkpoints/
文件: avenue_92.18.pth
```

#### 4.3 数据集（可选，用于测试）

MVTec 3D-AD数据集：

```
放入目录: datasets/mvtec3d/
结构:
datasets/mvtec3d/cable_gland/
├── train/good/rgb/xxx.png
├── train/good/xyz/xxx.tiff
└── test/xxx/rgb/xxx.png
```

### 5. 启动服务

```bash
# 终端1: API网关 (端口8001)
python services/gateway.py

# 终端2: 用户中心 (端口8003)
python backend_server.py
```

### 6. 访问界面

- 登录页面: http://localhost:8003/
- 工作台: http://localhost:8003/sandbox/

## 使用流程

### 方式一：数据驱动模式（推荐）

1. 访问 http://localhost:8003/sandbox/step1-entry.html
2. 选择"数据驱动编排"
3. 选择数据类型：
   - 点云+图像 → CFM模型（工业缺陷检测）
   - 视频+图像 → Jigsaw模型（异常事件检测）
4. 输入文件路径
5. 提交检测 → 查看异常分数和QoS映射
6. 查看编排结果

### 方式二：直接QoS编排

1. 访问 http://localhost:8003/sandbox/step1-entry.html
2. 选择"QoS向量编排"
3. 手动调整6维QoS参数
4. 提交编排

## QoS向量说明

系统使用6维QoS向量：

| 维度 | 参数 | 范围 | 说明 |
|------|------|------|------|
| 1 | 时延预算 | 0-1 | 端到端时延上限 |
| 2 | 抖动上限 | 0-1 | 时延抖动上限 |
| 3 | 丢包率 | 0-1 | 允许的最大丢包率 |
| 4 | 吞吐量 | 0-1 | 最低吞吐量要求 |
| 5 | 可靠性 | 0-1 | 可靠性等级 |
| 6 | 优先级 | 0-1 | 调度优先级 |

### 异常等级阈值

| 等级 | CFM分数 | Jigsaw分数 | 时延 | 可靠性 |
|------|--------|-----------|------|--------|
| CRITICAL | ≥0.7899 | ≥0.9763 | 50ms | 99.999% |
| HIGH | ≥0.765 | ≥0.917 | 200ms | 99.99% |
| MEDIUM | ≥0.7413 | ≥0.8306 | 1000ms | 99.9% |
| NORMAL | <0.7413 | <0.8306 | 5000ms | 99% |

## API接口

| 接口 | 方法 | 功能 |
|------|------|------|
| /api/orchestrate | POST | 提交编排 |
| /api/thresholds | GET | 获取异常阈值 |
| /api/resources | GET | 获取计算节点 |
| /api/links | GET | 获取网络链路 |
| /login | POST | 用户登录 |
| /register | POST | 用户注册 |

## 常见问题

### Q: 启动失败报"ModuleNotFoundError"

A: 确保已安装所有依赖：
```bash
pip install -r requirements.txt
```

### Q: 异常检测返回0.5

A: 检查模型权重文件是否正确放置在models/目录

### Q: 资源不足错误

A: 执行释放资源API或等待资源超时

### Q: 前端页面样式异常

A: 确保从正确端口访问（8003），并检查浏览器控制台错误

## 二次开发

### 训练新模型

```bash
# PPO训练
python train_ppo.py
```

### 修改QoS规则

编辑 `business_perception/qos_translator.py` 中的 BASE_RULES 字典

### 修改异常阈值

编辑 `threshold_config.json`

## 许可证

MIT License

## 联系方式

如有问题，请提交Issue：https://github.com/zhudun1/datadriven/issues