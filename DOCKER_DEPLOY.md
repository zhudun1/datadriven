# 服务器Docker部署步骤

## 一、部署架构分析

### 当前配置评估（已优化）

| 项目 | 现状 | 说明 |
|------|------|------|
| 容器数量 | 6个 (mysql, redis, usercenter, gateway, perception, orchestration) | ✅ 合理 |
| 依赖关系 | ✅ 已配置健康检查传递 | 优化完成 |
| 资源配置 | ✅ 已添加CPU/内存限制 | 优化完成 |
| 数据持久化 | ✅ MySQL + Redis + 模型目录 | 优化完成 |
| 网络模式 | bridge | 单一服务器足够 |

### 资源分配

| 服务 | CPU限制 | 内存限制 |
|------|---------|----------|
| mysql | 2核 | 2GB |
| redis | 1核 | 512MB |
| usercenter | 1核 | 1GB |
| gateway | 1核 | 512MB |
| perception | 2核 | 4GB |
| orchestration | 2核 | 4GB |

**总计: 9核, 约12GB内存**

---

## 二、服务器部署步骤

### 1. 服务器准备

```bash
# 推荐配置
CPU: 4核+
内存: 8GB+
磁盘: 50GB+
系统: Ubuntu 20.04+
```

### 2. 安装Docker和Docker Compose

```bash
# 安装Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 安装Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/download/v2.23.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

### 3. 上传项目文件

```bash
# 将整个项目上传到服务器
scp -r D:\data-driven_2 user@your-server:/opt/data-driven/
```

### 4. 构建基础镜像

```bash
cd /opt/data-driven/docker

# 构建基础镜像（只需一次，约5-10分钟）
docker build -f Dockerfile.base -t data-driven-base:latest ..

# 构建所有服务镜像
docker-compose build
```

### 5. 首次启动（需要训练模型）

```bash
cd /opt/data-driven/docker

# 启动所有服务
docker-compose up -d

# 查看编排服务日志（首次需要训练模型，约5分钟）
docker-compose logs -f orchestration
```

### 6. 验证服务

```bash
# 检查健康状态
curl http://localhost:8001/health
curl http://localhost:8003/health
```

---

## 三、日常运维

```bash
# 启动服务
docker-compose up -d

# 停止服务
docker-compose down

# 查看日志
docker-compose logs -f

# 重启单个服务
docker-compose restart orchestration

# 更新代码后重新构建
docker-compose build --no-cache
docker-compose up -d
```

## 四、数据持久化

- **MySQL数据**: `mysql_data` volume
- **Redis数据**: `redis_data` volume
- **PPO模型**: `./models` 目录（训练后保存）

---

## 五、关键注意事项

1. **编排服务首次启动需要训练PPO模型（约5分钟）** - 正常行为
2. **首次训练后模型会保存在 `./models` 目录**，重启后无需再训练
3. **如需修改Redis密码**，需同时修改：
   - `docker-compose.yml` 中的 `command`
   - 各服务的环境变量 `REDIS_PASSWORD`