# 编排资源保障功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当编排请求资源不足时，检测5秒内是否有资源释放，返回前端让用户选择等待或取消

**Architecture:**
- 新增资源检查API `/api/orchestrate/check`: 预检查资源是否充足，返回状态
- 修改编排API增加wait参数: 支持前端传入等待选项
- 修改响应格式: 返回waiting/insufficient状态供前端决策

**Tech Stack:** Python FastAPI, pymysql, 前端HTML/CSS/JS

---

### Task 1: 修改编排服务 - 添加资源检查方法

**Files:**
- Modify: `intelligent_orchestration/orchestration_service.py`

- [ ] **Step 1: 添加资源预检查方法**

在 `OrchestrationService` 类中添加资源检查方法 `check_resources()`:

```python
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
```

- [ ] **Step 2: 验证代码语法正确**

Run: `python -c "from intelligent_orchestration.orchestration_service import OrchestrationService; print('OK')"`

- [ ] **Step 3: 提交**

```bash
git add intelligent_orchestration/orchestration_service.py
git commit -m "feat: 添加资源预检查方法 check_resources()"
```

---

### Task 2: 修改API网关 - 添加检查端点

**Files:**
- Modify: `services/gateway.py:804-880`

- [ ] **Step 1: 添加检查端点**

在 `gateway.py` 中添加新的数据模型和端点:

```python
class OrchCheckRequest(BaseModel):
    qos_vector: list = [0.8, 0.1, 1.0, 0.05]
    resource_request: dict = {"vcpu": 8, "memory": 16}
    vnf_count: int = 2
    model_type: str = "CFM"


@app.post("/api/orchestrate/check")
def orchestrate_check(req: OrchCheckRequest):
    """预检查资源状态，返回等待/不足/充足"""
    from intelligent_orchestration.orchestration_service import OrchestrationService

    global _orchestration_service
    if 'service' not in _orchestration_service:
        _orchestration_service['service'] = OrchestrationService()
    service = _orchestration_service['service']

    result = service.check_resources(
        qos_vector=req.qos_vector,
        resource_req=req.resource_request,
        vnf_count=req.vnf_count,
    )
    return result
```

- [ ] **Step 2: 验证导入**

Run: `python -c "from services.gateway import app; print('OK')"`

- [ ] **Step 3: 提交**

```bash
git add services/gateway.py
git commit -m "feat: 添加 /api/orchestrate/check 端点"
```

---

### Task 3: 修改前端 - 资源状态提示界面

**Files:**
- Modify: `frontend/sandbox/step2-qos-mapping.html`

- [ ] **Step 1: 添加检查按钮和弹窗**

在 `step2-qos-mapping.html` 的交互演示区添加"检查资源"按钮和资源状态弹窗:

```html
<!-- 检查资源按钮 -->
<button class="btn" onclick="checkResources()">检查资源状态</button>

<!-- 资源状态弹窗 -->
<div id="resourceModal" class="modal" style="display:none;">
    <div class="modal-content">
        <h2 id="modalTitle">资源状态</h2>
        <p id="modalMessage"></p>

        <div id="modalOptions" style="display:none;">
            <button class="btn" onclick="waitAndOrchestrate()">等待编排</button>
            <button class="btn secondary" onclick="addResource()">添加资源</button>
            <button class="btn danger" onclick="closeModal()">取消</button>
        </div>

        <div id="modalSufficient" style="display:none;">
            <button class="btn" onclick="startOrchestration()">开始编排</button>
            <button class="btn secondary" onclick="closeModal()">取消</button>
        </div>
    </div>
</div>
```

- [ ] **Step 2: 添加JavaScript逻辑**

```javascript
let pendingCheckResult = null;

async function checkResources() {
    const data = {
        qos_vector: [0.8, 0.1, 1.0, 0.05],
        resource_request: {vcpu: 8, memory: 16},
        vnf_count: 2,
    };

    try {
        const res = await fetch('/api/orchestrate/check', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data),
        });
        const result = await res.json();

        pendingCheckResult = result;
        showResourceModal(result);
    } catch (e) {
        alert('检查失败: ' + e.message);
    }
}

function showResourceModal(result) {
    const modal = document.getElementById('resourceModal');
    const title = document.getElementById('modalTitle');
    const message = document.getElementById('modalMessage');
    const optionsDiv = document.getElementById('modalOptions');
    const sufficientDiv = document.getElementById('modalSufficient');

    modal.style.display = 'block';

    if (result.status === 'waiting') {
        title.textContent = '资源即将释放';
        title.style.color = '#ffa502';
        message.textContent = result.message;
        optionsDiv.style.display = 'block';
        sufficientDiv.style.display = 'none';
    } else if (result.status === 'insufficient') {
        title.textContent = '资源不足';
        title.style.color = '#ff4757';
        message.textContent = result.message +
            '\n需要: vcpu=' + result.required.vcpu + ', memory=' + result.required.memory_gb +
            '\n可用: vcpu=' + result.available.vcpu + ', memory=' + result.available.memory_gb;
        optionsDiv.style.display = 'block';
        sufficientDiv.style.display = 'none';
    } else {
        title.textContent = '资源充足';
        title.style.color = '#2ed573';
        message.textContent = result.message;
        optionsDiv.style.display = 'none';
        sufficientDiv.style.display = 'block';
    }
}

function closeModal() {
    document.getElementById('resourceModal').style.display = 'none';
}

function waitAndOrchestrate() {
    closeModal();
    // 调用编排接口，带wait标志
    startOrchestration();
}

function addResource() {
    closeModal();
    // 跳转添加资源页面
    window.location.href = 'step6-result.html';
}

function startOrchestration() {
    // 执行编排
    calculateQoS();
}
```

- [ ] **Step 3: 提交**

```bash
git add frontend/sandbox/step2-qos-mapping.html
git commit -m "feat: 添加资源检查弹窗界面"
```

---

### Task 4: 集成测试

**Files:**
- Test: `test_api_flow.py` (已存在)

- [ ] **Step 1: 运行测试**

Run: `python test_api_flow.py`

- [ ] **Step 2: 验证检查端点**

手动测试 `/api/orchestrate/check` 端点

- [ ] **Step 3: 提交**

```bash
git commit -m "test: 验证资源检查功能"
```

---

### 实现顺序

1. Task 1: 编排服务添加检查方法
2. Task 2: API网关添加端点
3. Task 3: 前端界面
4. Task 4: 集成测试

每个任务完成后提交一次，保持小而清晰的提交历史。