# QoS Frontend

本前端按需求实现：

- 登录/注册切换页面
- 登录字段：用户名（邮箱/手机号）+ 密码校验
- 注册字段：用户名称、手机号、短信验证码、邮箱、密码与确认密码
- 密码默认隐藏，可切换显示
- 登录后进入两步输入流程：
  - 步骤 1 上传业务感知图片
  - 步骤 2 填写虚拟计算资源需求
- 后端返回结果展示 + 下载按钮

## 目录

- `index.html`: 登录/注册页面
- `app.html`: 编排工作台
- `js/config.js`: 后端接口配置
- `js/api.js`: 通用请求封装
- `js/auth.js`: 登录/注册逻辑
- `js/workflow.js`: 两步输入与下载逻辑

## 本地启动

在 `qos-frontend` 目录执行：

```bash
python3 -m http.server 8080
```

浏览器访问：

- `http://localhost:8080/index.html`

## 接口对接

默认接口：

- `POST /api/auth/login`
- `POST /api/auth/register`
- `POST /api/auth/send-code`
- `POST /api/orchestration/run`

如果后端地址不同，在 `js/config.js` 修改 `BASE_URL` 和 `ENDPOINTS`。

## 对接 data-driven 后端

先启动后端（终端 1）：

```bash
cd /Users/haokexin/Documents/data-driven_2
python3 backend_server.py
```

再启动前端静态服务（终端 2）：

```bash
cd /Users/haokexin/Documents/data-driven_2/frontend
python3 -m http.server 8080
```

访问：

- `http://localhost:8080/index.html`

当前默认 `js/config.js` 已配置后端为 `http://127.0.0.1:8000`，并且关闭了本地回退（`ENABLE_LOCAL_FALLBACK: false`），请求将只走真实后端接口。
