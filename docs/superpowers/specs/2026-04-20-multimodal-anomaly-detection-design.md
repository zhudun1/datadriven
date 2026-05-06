# 多模型异常检测系统设计

**日期**: 2026-04-20

## 目标

扩展现有系统，支持 CFM 和 Jigsaw 两种异常检测模型，用户可选择模型并上传对应数据。

## 系统架构

```
前端 → Gateway → 消息队列 → 感知服务 → 编排服务
```

## 数据表更改

文件: `docker/mysql/init.sql`

```sql
ALTER TABLE t_industrial_data ADD COLUMN data_type ENUM('image_pc','video_image') DEFAULT 'image_pc';
ALTER TABLE t_industrial_data ADD COLUMN model_type VARCHAR(32) DEFAULT 'CFM';
```

## 接口约定

### Gateway 请求体

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

### 消息队列消息格式

```python
{
    "task_id": str,
    "data_id": int,
    "model_type": str,  # "CFM" 或 "Jigsaw"
    "rgb_path": str,           # CFM模式
    "pcd_path": str,          # CFM模式
    "video_path": str,         # Jigsaw模式
    "aux_image_path": str,     # Jigsaw模式(可选)
    "resource_request": dict
}
```

### 编排队列返回格式

```python
{
    "task_id": str,
    "data_id": int,
    "anomaly_score": float,
    "risk_level": str,
    "qos_vector": list,
    "model_type": str  # 记录使用的模型
}
```

## 实施顺序

1. **感知服务修改**
   - 添加 JigsawVADDetector 类
   - 修改 PerceptionService 支持模型选择
   - 添加模型选择加载逻辑

2. **Gateway 修改**
   - 扩展 OrchRequest 模型
   - 扩展 write_industrial_data 函数
   - 添加 model_type 字段传递

3. **前端修改**
   - 添加模型选择下拉框
   - 添加强制数据输入验证
   - 根据模型类型动态显示输入字段