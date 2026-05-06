# 多模型异常检测系统实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标:** 在感知服务中添加 Jigsaw 模型支持，扩展 Gateway 接口，修改前端界面，实现 CFM 和 Jigsaw 两种异常检测模型的可选切换。

**架构:** 系统通过消息队列通信，先修改感知服务添加 Jigsaw 检测器，再修改 Gateway 扩展接口，最后修改前端的模型选择和数据输入界面。

**涉及文件:**
- `business_perception/jigsaw_detector.py` (新建)
- `services/perception_service.py` (修改)
- `services/gateway.py` (修改)
- `docker/mysql/init.sql` (修改)
- `frontend/app.html` (修改)
- `frontend/js/workflow.js` (修改)
- `frontend/js/api.js` (修改)

---

## 文件结构映射

| 文件 | 职责 |
|------|------|
| `business_perception/jigsaw_detector.py` | JigsawVAD 检测器实现 |
| `services/perception_service.py` | 感知服务，支持模型选择 |
| `services/gateway.py` | API网关，扩展请求模型 |
| `docker/mysql/init.sql` | 数据库表扩展 |
| `frontend/app.html` | 前端UI，模型选择和数据输入 |
| `frontend/js/workflow.js` | 前端逻辑，处理数据上传 |
| `frontend/js/api.js` | API调用封装 |

---

### Task 1: 添加 JigsawVADDetector 检测器

**Files:**
- Create: `business_perception/jigsaw_detector.py`

需要从 `crossmodal-feature-mapping/business_perception/jigsaw_detector.py` 复制并适配路径。

- [ ] **Step 1: 创建 JigsawVADDetector 类**

基于 `crossmodal-feature-mapping/business_perception/jigsaw_detector.py` 中的实现，创建 `business_perception/jigsaw_detector.py`。

需要修改的路径：
- JIGSAW_PATH 改为相对路径 `os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/Jigsaw-VAD-main"`
- 添加 `checkpoint_path` 参数支持

```python
"""
Jigsaw-VAD 异常检测器
用于视频和图像的异常检测
"""

import os
import sys
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Union, Dict, List
import cv2

class JigsawVADDetector:
    def __init__(self, checkpoint_path: str, time_length: int = 7, device: str = "cuda"):
        self.device = device if torch.cuda.is_available() else "cpu"
        self.time_length = time_length
        self.half_t = time_length // 2

        # 添加 Jigsaw-VAD-main 路径
        JIGSAW_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Jigsaw-VAD-main")
        sys.path.insert(0, JIGSAW_PATH)

        from models.model import WideBranchNet

        self.model = WideBranchNet(
            time_length=time_length,
            num_classes=[time_length ** 2, 81]
        ).to(self.device)

        if os.path.exists(checkpoint_path):
            state_dict = torch.load(checkpoint_path, map_location=self.device)
            self.model.load_state_dict(state_dict, strict=True)
            print(f"Loaded Jigsaw-VAD checkpoint from {checkpoint_path}")

        self.model.eval()

    def preprocess_frame(self, frame, target_size=64):
        frame = cv2.resize(frame, (target_size, target_size))
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame = frame.astype(np.float32) / 255.0
        frame = torch.from_numpy(frame).permute(2, 0, 1)
        return frame

    def extract_frames_sequence(self, video_path, center_frame, bbox=None):
        frames = []
        cap = cv2.VideoCapture(video_path)

        for f in range(center_frame - self.half_t, center_frame + self.half_t + 1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ret, frame = cap.read()
            if not ret:
                frame = np.zeros((240, 360, 3), dtype=np.uint8)

            if bbox is not None:
                x1, y1, x2, y2 = map(int, bbox)
                h, w = frame.shape[:2]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                frame = frame[y1:y2, x1:x2]

            frame = self.preprocess_frame(frame)
            frames.append(frame)

        cap.release()
        frames = torch.stack(frames, dim=1)  # (C, T, H, W)
        return frames

    def compute_jigsaw_score(self, frame_sequence):
        obj = frame_sequence.unsqueeze(0).to(self.device)

        with torch.no_grad():
            temp_logits, spat_logits = self.model(obj)

            temp_logits = temp_logits.view(-1, self.time_length, self.time_length)
            spat_logits = spat_logits.view(-1, 9, 9)

            spat_probs = F.softmax(spat_logits, -1)
            diag = torch.diagonal(spat_probs, offset=0, dim1=-2, dim2=-1)
            spatial_conf = diag.min(-1)[0].cpu().item()

            temp_probs = F.softmax(temp_logits, -1)
            diag2 = torch.diagonal(temp_probs, offset=0, dim1=-2, dim2=-1)
            temporal_conf = diag2.min(-1)[0].cpu().item()

        return {
            'spatial_confidence': spatial_conf,
            'temporal_confidence': temporal_conf,
            'spatial_score': 1 - spatial_conf,
            'temporal_score': 1 - temporal_conf,
            'combined_score': 1 - (spatial_conf + temporal_conf) / 2
        }

    def detect_video(self, video_path):
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        start_frame = self.half_t
        end_frame = total_frames - self.half_t

        frame_scores = []
        for frame_idx in range(start_frame, min(end_frame, start_frame + 100)):
            seq = self.extract_frames_sequence(video_path, frame_idx)
            scores = self.compute_jigsaw_score(seq)
            frame_scores.append(scores['combined_score'])

        if not frame_scores:
            return 0.5

        return float(np.mean(frame_scores))

    def detect_image(self, image_path):
        frame = cv2.imread(image_path)
        if frame is None:
            return 0.5

        frame = self.preprocess_frame(frame)
        frames = torch.stack([frame] * self.time_length, dim=1)
        scores = self.compute_jigsaw_score(frames)

        return scores['combined_score']

    def get_anomaly_score(self, video_path=None, image_path=None):
        if video_path:
            return self.detect_video(video_path)
        elif image_path:
            return self.detect_image(image_path)
        else:
            return 0.5
```

- [ ] **Step 2: 提交**

```bash
git add business_perception/jigsaw_detector.py
git commit -m "feat: add JigsawVADDetector for video/image anomaly detection"
```

---

### Task 2: 修改 PerceptionService 支持模型选择

**Files:**
- Modify: `services/perception_service.py:86-111` (_ensure_models 方法)
- Modify: `services/perception_service.py:136-189` (process 方法)

- [ ] **Step 1: 修改 _ensure_models 方法支持 model_type 参数**

修改 `_ensure_models` 方法，添加 `model_type` 参数：

```python
def _ensure_models(self, model_type="CFM"):
    """延迟加载模型，根据 model_type 选择"""
    if self.detector is None or self.current_model_type != model_type:
        try:
            if model_type == "CFM":
                from business_perception.cfm_detector import CFMDetector
                self.detector = CFMDetector(CLASS_NAME, CHECKPOINT_PATH, "cpu")
            elif model_type == "Jigsaw":
                from business_perception.jigsaw_detector import JigsawVADDetector
                # Jigsaw checkpoint 路径
                jigsaw_checkpoint = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "Jigsaw-VAD-main", "checkpoint", "best.pth"
                )
                self.detector = JigsawVADDetector(jigsaw_checkpoint, time_length=7, device="cpu")

            self.current_model_type = model_type
            logger.info(f"模型加载完成（{model_type}模式, CPU）")
        except Exception as e:
            logger.error(f"模型加载失败: {e}")
            self.detector = None
            self.current_model_type = None
            logger.warning("感知服务降级为仅传递消息模式")
```

在 `__init__` 中添加 `self.current_model_type = None`

- [ ] **Step 2: 修改 process 方法处理不同模型类型**

修改 `process` 方法，根据 `model_type` 路由到不同的检测逻辑：

```python
def process(self, message: dict) -> dict:
    self._ensure_models(message.get("model_type", "CFM"))

    task_id = message.get("task_id", "unknown")
    data_id = message.get("data_id", 0)
    model_type = message.get("model_type", "CFM")

    logger.info(f"[{task_id}] 开始处理 (模型: {model_type})")

    try:
        if self.detector is None:
            logger.warning(f"[{task_id}] 感知服务降级模式，使用默认 QoS 向量")
            anomaly_score = 0.5
            risk_level = "medium"
            qos_list = [10.0, 5.0, 1, 0.01]
        else:
            if model_type == "CFM":
                rgb_path = message.get("rgb_path", "")
                pcd_path = message.get("pcd_path", "")
                rgb, pc = load_rgb_pc(rgb_path, pcd_path)
                anomaly_score = self.detector.get_anomaly_score(rgb, pc)
            elif model_type == "Jigsaw":
                video_path = message.get("video_path", "")
                aux_image_path = message.get("aux_image_path", "")
                if video_path:
                    anomaly_score = self.detector.get_anomaly_score(video_path=video_path)
                else:
                    anomaly_score = self.detector.get_anomaly_score(image_path=aux_image_path)
            else:
                anomaly_score = 0.5

            logger.info(f"[{task_id}] 异常分数: {anomaly_score:.4f}")

            risk_level, qos_vector = self.translator.translate(float(anomaly_score))
            qos_list = qos_vector.round(4).tolist()
            logger.info(f"[{task_id}] 风险等级: {risk_level}, QoS向量: {qos_list}")

        write_perception_result(data_id, anomaly_score, risk_level, qos_list)

        orch_message = {
            "task_id": task_id,
            "data_id": data_id,
            "anomaly_score": round(float(anomaly_score), 4),
            "risk_level": risk_level,
            "qos_vector": qos_list,
            "model_type": model_type,
            "resource_request": message.get("resource_request", {})
        }
        self.mq.publish(MessageQueue.QUEUE_ORCHESTRATION, orch_message)
        logger.info(f"[{task_id}] 已发送到编排队列")

        return {"status": "success", "task_id": task_id}

    except Exception as e:
        logger.error(f"[{task_id}] 处理失败: {str(e)}", exc_info=True)
        return {"status": "error", "task_id": task_id, "error": str(e)}
```

- [ ] **Step 3: 提交**

```bash
git add services/perception_service.py
git commit -m "feat: add model selection support in PerceptionService"
```

---

### Task 3: 修改 Gateway 扩展接口

**Files:**
- Modify: `services/gateway.py:42-46` (OrchRequest 模型)
- Modify: `services/gateway.py:68-86` (write_industrial_data 函数)
- Modify: `services/gateway.py:96-133` (submit_orchestration 函数)

- [ ] **Step 1: 扩展 OrchRequest 模型**

```python
class OrchRequest(BaseModel):
    model_type: str = "CFM"  # 或 "Jigsaw"
    # CFM模式
    businessImagePath: Optional[str] = None
    pointCloudPath: Optional[str] = None
    # Jigsaw模式
    videoPath: Optional[str] = None
    auxiliaryImagePath: Optional[str] = None
    # 通用
    resourceRequest: Optional[dict] = {}
```

- [ ] **Step 2: 修改 write_industrial_data 函数**

```python
def write_industrial_data(req: OrchRequest) -> int:
    import pymysql
    from datetime import datetime

    conn_cfg = {...}
    conn = pymysql.connect(**conn_cfg)
    with conn.cursor() as c:
        c.execute(
            "INSERT INTO t_industrial_data (rgb_path, pcd_path, video_path, aux_image_path, model_type, data_type, create_time) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                req.businessImagePath or "",
                req.pointCloudPath or "",
                req.videoPath or "",
                req.auxiliaryImagePath or "",
                req.model_type,
                "video_image" if req.model_type == "Jigsaw" else "image_pc",
                datetime.now()
            )
        )
        conn.commit()
        data_id = int(c.lastrowid)
    conn.close()
    return data_id
```

- [ ] **Step 3: 修改 submit_orchestration 函数**

```python
@app.post("/pipeline")
def submit_orchestration(
    req: OrchRequest,
    authorization: Optional[str] = Header(None)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")

    try:
        mq = get_message_queue()

        # 验证请求参数
        if req.model_type == "CFM":
            if not req.businessImagePath or not req.pointCloudPath:
                raise HTTPException(status_code=400, detail="CFM模式需要图片和点云路径")
        elif req.model_type == "Jigsaw":
            if not req.videoPath and not req.auxiliaryImagePath:
                raise HTTPException(status_code=400, detail="Jigsaw模式需要视频或图像路径")

        data_id = write_industrial_data(req)
        logger.info(f"工业数据写入成功: data_id={data_id}")

        # 构建消息
        message = {
            "task_id": str(data_id),
            "data_id": data_id,
            "model_type": req.model_type,
            "resource_request": req.resourceRequest or {}
        }

        if req.model_type == "CFM":
            message["rgb_path"] = req.businessImagePath
            message["pcd_path"] = req.pointCloudPath
        else:  # Jigsaw
            message["video_path"] = req.videoPath or ""
            message["aux_image_path"] = req.auxiliaryImagePath or ""

        mq.publish(MessageQueue.QUEUE_PERCEPTION, message)
        logger.info(f"任务已提交到感知队列: task_id={message['task_id']}")

        result = mq.get_result(message["task_id"], timeout=120)

        if result is None:
            return {"status": "timeout", "message": "处理超时"}

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"提交任务失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 4: 提交**

```bash
git add services/gateway.py
git commit -m "feat: extend Gateway API for CFM and Jigsaw models"
```

---

### Task 4: 修改数据库表

**Files:**
- Modify: `docker/mysql/init.sql`

- [ ] **Step 1: 添加表字段**

在 `t_industrial_data` 表中添加 `data_type` 和 `model_type` 字段：

```sql
ALTER TABLE t_industrial_data ADD COLUMN data_type ENUM('image_pc','video_image') DEFAULT 'image_pc';
ALTER TABLE t_industrial_data ADD COLUMN model_type VARCHAR(32) DEFAULT 'CFM';
```

- [ ] **Step 2: 提交**

```bash
git add docker/mysql/init.sql
git commit -m "feat: extend industrial_data table for model selection"
```

---

### Task 5: 修改前端界面

**Files:**
- Modify: `frontend/app.html`
- Modify: `frontend/js/workflow.js`
- Modify: `frontend/js/api.js`

- [ ] **Step 1: 修改 app.html 添加模型选择和数据输入**

在 step1-form 中添加强制选择和数据输入区域：

```html
<form id="step1-form" novalidate>
  <label class="field">
    <span>选择异常检测模型</span>
    <select id="model-type" required>
      <option value="CFM">CFM (图像 + 点云)</option>
      <option value="Jigsaw">Jigsaw (视频 + 图像)</option>
    </select>
  </label>

  <!-- CFM模式输入 -->
  <div id="cfm-inputs" class="model-inputs">
    <label class="field">
      <span>业务图片路径</span>
      <input id="business-image-path" type="text" placeholder="/path/to/image.jpg" />
    </label>
    <label class="field">
      <span>点云文件路径</span>
      <input id="point-cloud-path" type="text" placeholder="/path/to/pointcloud.tiff" />
    </label>
  </div>

  <!-- Jigsaw模式输入 -->
  <div id="jigsaw-inputs" class="model-inputs" style="display:none;">
    <label class="field">
      <span>视频文件路径 (可选)</span>
      <input id="video-path" type="text" placeholder="/path/to/video.avi" />
    </label>
    <label class="field">
      <span>辅助图像路径</span>
      <input id="aux-image-path" type="text" placeholder="/path/to/image.jpg" />
    </label>
  </div>

  <button type="submit" class="secondary-btn">保存步骤 1</button>
</form>
```

- [ ] **Step 2: 修改 workflow.js 添加模型选择逻辑**

```javascript
const modelTypeSelect = document.getElementById("model-type");
const cfmInputs = document.getElementById("cfm-inputs");
const jigsawInputs = document.getElementById("jigsaw-inputs");

modelTypeSelect.addEventListener("change", () => {
  const type = modelTypeSelect.value;
  if (type === "CFM") {
    cfmInputs.style.display = "block";
    jigsawInputs.style.display = "none";
  } else {
    cfmInputs.style.display = "none";
    jigsawInputs.style.display = "block";
  }
});

step1Form.addEventListener("submit", (event) => {
  event.preventDefault();
  setMessage("");

  const modelType = modelTypeSelect.value;
  let dataPath1, dataPath2;

  if (modelType === "CFM") {
    dataPath1 = document.getElementById("business-image-path").value.trim();
    dataPath2 = document.getElementById("point-cloud-path").value.trim();
    if (!dataPath1 || !dataPath2) {
      setMessage("请填写图片和点云路径", "error");
      return;
    }
  } else {
    dataPath1 = document.getElementById("video-path").value.trim();
    dataPath2 = document.getElementById("aux-image-path").value.trim();
    if (!dataPath1 && !dataPath2) {
      setMessage("请填写视频或图像路径", "error");
      return;
    }
  }

  step1Done = true;
  setMessage("步骤 1 已完成，请继续填写资源需求", "success");
});
```

修改 step2Form 提交，为FormData 添加正确的字段：

```javascript
step2Form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("");

  if (!step1Done) {
    setMessage("请先完成步骤 1", "error");
    return;
  }

  const modelType = document.getElementById("model-type").value;
  const vcpu = Number(document.getElementById("vcpu").value);
  const memory = Number(document.getElementById("memory").value);
  const storage = Number(document.getElementById("storage").value);
  const bandwidth = Number(document.getElementById("bandwidth").value);

  if ([vcpu, memory, storage, bandwidth].some(v => Number.isNaN(v) || v <= 0)) {
    setMessage("资源参数必须是大于 0 的数字", "error");
    return;
  }

  const resources = { vcpu, memory, storage, bandwidth };

  // 构建请求数据
  const requestData = {
    modelType: modelType,
    resourceRequest: resources
  };

  if (modelType === "CFM") {
    requestData.businessImagePath = document.getElementById("business-image-path").value.trim();
    requestData.pointCloudPath = document.getElementById("point-cloud-path").value.trim();
  } else {
    requestData.videoPath = document.getElementById("video-path").value.trim();
    requestData.auxiliaryImagePath = document.getElementById("aux-image-path").value.trim();
  }

  // 使用 JSON 发送
  const response = await fetch(API_CONFIG.GATEWAY_URL + "/pipeline", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${localStorage.getItem("qos_token")}`
    },
    body: JSON.stringify(requestData)
  });

  // 处理响应...
});
```

- [ ] **Step 3: 提交**

```bash
git add frontend/app.html frontend/js/workflow.js
git commit -m "feat: add model selection UI for CFM and Jigsaw"
```

---

## 实施检查

**Spec 覆盖检查:**
- [x] 感知服务添加 JigsawVADDetector - Task 1
- [x] 感知服务支持模型选择 - Task 2
- [x] Gateway 扩展接口 - Task 3
- [x] 数据库表扩展 - Task 4
- [x] 前端界面修改 - Task 5

**类型一致性检查:**
- `model_type` 字段在各文件中一致使用
- `video_path` / `aux_image_path` 字段名与 Gateway 一致

**Placeholder 扫描:**
- 无 TBD/TODO
- 代码完整

---

## 执行选择

Plan complete and saved to `docs/superpowers/plans/2026-04-20-multimodal-anomaly-detection.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**