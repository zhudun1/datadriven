import { API_CONFIG } from "../js/config.js";

const pathForm = document.getElementById("path-form");
const resourceForm = document.getElementById("resource-form");
const rgbPathInput = document.getElementById("business-image-path");
const pointCloudPathInput = document.getElementById("point-cloud-path");
const messageEl = document.getElementById("orchestration-message");
const resultEl = document.getElementById("orchestration-result");
const downloadBtn = document.getElementById("download-btn");
const logoutBtn = document.getElementById("logout-btn");
const resourceProfileSelect = document.getElementById("resource-profile");
const refreshResourceBtn = document.getElementById("refresh-resource-btn");
const resourceLoadMessageEl = document.getElementById("resource-load-message");

let pathSaved = false;
let savedPaths = { businessImagePath: "", pointCloudPath: "" };
let downloadableBlob = null;
let activeResources = [];

function setMessage(text, type = "") {
  messageEl.textContent = text;
  messageEl.className = `message ${type}`.trim();
}

function getAuthToken() {
  return localStorage.getItem("qos_token") || "";
}

function requireLogin() {
  const token = localStorage.getItem("qos_token");
  const user = localStorage.getItem("qos_user");
  if (!token && !user) {
    window.location.href = "../index.html";
  }
}

function parseResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

function setResourceLoadMessage(text, type = "") {
  if (!resourceLoadMessageEl) {
    return;
  }
  resourceLoadMessageEl.textContent = text;
  resourceLoadMessageEl.className = `message ${type}`.trim();
}

// ========== 物理资源可视化 ==========
const canvas = document.getElementById("resource-canvas");
const visualizationMessageEl = document.getElementById("visualization-message");

function setVisualizationMessage(text, type = "") {
  if (!visualizationMessageEl) return;
  visualizationMessageEl.textContent = text;
  visualizationMessageEl.className = `message ${type}`.trim();
}

function parseMaybeJSON(value) {
  if (!value) {
    return {};
  }
  if (typeof value === "object") {
    return value;
  }
  if (typeof value !== "string") {
    return {};
  }
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

function getResourceState(resource) {
  return parseMaybeJSON(resource?.current_state);
}

function renderResourceVisualization() {
  if (!canvas) {
    return;
  }

  const ctx = canvas.getContext("2d");
  const container = document.getElementById("visualization-container");

  // 设置 canvas 尺寸
  const rect = container.getBoundingClientRect();
  canvas.width = rect.width;
  canvas.height = 400;

  // 清空画布
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // 如果没有资源，显示提示
  if (!activeResources || activeResources.length === 0) {
    ctx.fillStyle = "#5d6470";
    ctx.font = "14px Arial";
    ctx.textAlign = "center";
    ctx.fillText("暂无物理资源，请添加节点和链路", canvas.width / 2, canvas.height / 2);
    return;
  }

  // 分离节点和链路
  const nodes = [];
  const links = [];

  for (const resource of activeResources) {
    const state = getResourceState(resource);
    const type = resource?.resource_type || state?.type;

    // 解析节点ID (从 resource_id 如 "node-0" 提取数字 0)
    let nodeId = 0;
    if (resource?.resource_id) {
      const match = resource.resource_id.match(/(\d+)$/);
      if (match) {
        nodeId = parseInt(match[1], 10);
      }
    }

    if (type === "node" || type === "compute") {
      nodes.push({
        id: nodeId,
        name: resource?.resource_name || `节点 ${nodeId}`,
        vcpu: state?.vcpu || 0,
        memory_gb: state?.memory_gb || 0,
        storage: state?.storage || 0,
        bandwidth: state?.bandwidth || 0,
        cpu_load: state?.cpu || state?.cpu_load || 0,
        memory_load: state?.memory || state?.memory_load || 0,
      });
    } else if (type === "link" || type === "edge" || type === "network") {
      links.push({
        src: state?.src_node ?? state?.src ?? 0,
        dst: state?.dst_node ?? state?.dst ?? 0,
        bandwidth: state?.bandwidth || 0,
        latency: state?.latency || 0,
      });
    }
  }

  // 排序节点
  nodes.sort((a, b) => a.id - b.id);

  if (nodes.length === 0) {
    ctx.fillStyle = "#5d6470";
    ctx.font = "14px Arial";
    ctx.textAlign = "center";
    ctx.fillText("暂无节点资源", canvas.width / 2, canvas.height / 2);
    return;
  }

  // 计算节点布局 - 圆形布局
  const centerX = canvas.width / 2;
  const centerY = canvas.height / 2;
  const radius = Math.min(canvas.width, canvas.height) * 0.35;

  const nodePositions = {};

  for (let i = 0; i < nodes.length; i++) {
    const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
    nodePositions[nodes[i].id] = {
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
    };
  }

  // 绘制链路（先画链路，在节点下方）
  ctx.strokeStyle = "#94a3b8";
  ctx.lineWidth = 2;
  ctx.fillStyle = "#64748b";
  ctx.font = "12px Arial";
  ctx.textAlign = "center";

  for (const link of links) {
    const srcPos = nodePositions[link.src];
    const dstPos = nodePositions[link.dst];

    if (!srcPos || !dstPos) continue;

    // 绘制连线
    ctx.beginPath();
    ctx.moveTo(srcPos.x, srcPos.y);
    ctx.lineTo(dstPos.x, dstPos.y);
    ctx.stroke();

    // 计算中点
    const midX = (srcPos.x + dstPos.x) / 2;
    const midY = (srcPos.y + dstPos.y) / 2;

    // 绘制带宽标签背景
    const bandwidthText = `带宽: ${link.bandwidth}`;
    const textMetrics = ctx.measureText(bandwidthText);
    const padding = 4;

    ctx.fillStyle = "#fff";
    ctx.fillRect(
      midX - textMetrics.width / 2 - padding,
      midY - 10 - padding,
      textMetrics.width + padding * 2,
      20 + padding
    );

    // 绘制带宽文字
    ctx.fillStyle = "#1d4ed8";
    ctx.fillText(bandwidthText, midX, midY);
  }

  // 绘制节点（圆圈）
  for (const node of nodes) {
    const pos = nodePositions[node.id];
    if (!pos) continue;

    // 绘制圆圈
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, 30, 0, 2 * Math.PI);
    ctx.fillStyle = "#3b82f6";
    ctx.fill();
    ctx.strokeStyle = "#1d4ed8";
    ctx.lineWidth = 2;
    ctx.stroke();

    // 绘制节点名称
    ctx.fillStyle = "#111111";
    ctx.font = "bold 14px Arial";
    ctx.textAlign = "center";
    ctx.fillText(node.name, pos.x, pos.y - 40);

    // 绘制节点信息
    ctx.fillStyle = "#5d6470";
    ctx.font = "11px Arial";
    ctx.fillText(`CPU: ${node.vcpu}`, pos.x, pos.y + 45);
    ctx.fillText(`内存: ${node.memory_gb}GB`, pos.x, pos.y + 58);
  }

  setVisualizationMessage(`可视化: ${nodes.length} 个节点, ${links.length} 条链路`, "success");
}

function toPositiveNumber(value, fallback = 0) {
  const num = Number(value);
  return Number.isFinite(num) && num > 0 ? num : fallback;
}

function pickResourceNumeric(resource, stateKeys, topLevelKeys, fallback = 0) {
  const state = getResourceState(resource);
  for (const key of stateKeys) {
    const value = toPositiveNumber(state?.[key], 0);
    if (value > 0) {
      return value;
    }
  }
  for (const key of topLevelKeys) {
    const value = toPositiveNumber(resource?.[key], 0);
    if (value > 0) {
      return value;
    }
  }
  return fallback;
}

function getResourceLabel(resource) {
  const id = resource?.resource_id || resource?.id || "unknown";
  const name = resource?.resource_name || resource?.name || id;
  const type = resource?.resource_type || resource?.type || "resource";
  return `${name} (${type}) [${id}]`;
}

function renderResourceOptions(resources) {
  if (!resourceProfileSelect) {
    return;
  }

  resourceProfileSelect.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "请选择数据库中的可用资源";
  resourceProfileSelect.appendChild(placeholder);

  for (const resource of resources) {
    const option = document.createElement("option");
    option.value = String(resource?.resource_id || resource?.id || "");
    option.textContent = getResourceLabel(resource);
    resourceProfileSelect.appendChild(option);
  }
}

async function loadActiveResources() {
  const token = getAuthToken();
  if (!token) {
    setResourceLoadMessage("未检测到登录态，请重新登录", "error");
    return;
  }

  setResourceLoadMessage("正在加载数据库资源...");
  try {
    // 资源查询走网关
    const response = await fetch(`${API_CONFIG.GATEWAY_URL}${API_CONFIG.ENDPOINTS.activeResources}`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
    const data = await parseResponse(response);
    if (!response.ok) {
      const msg = typeof data === "object" && data?.message ? data.message : "读取资源失败";
      throw new Error(msg);
    }

    const resources = Array.isArray(data?.resources) ? data.resources : [];
    activeResources = resources;
    renderResourceOptions(resources);
    // 同时更新删除资源下拉框
    renderDeleteResourceOptions(resources);
    if (resources.length > 0) {
      setResourceLoadMessage(`已加载 ${resources.length} 条数据库资源`, "success");
    } else {
      setResourceLoadMessage("数据库暂无可用资源，请先添加节点和链路", "error");
    }
  } catch (error) {
    setResourceLoadMessage(error.message || "读取资源失败", "error");
  }
}

requireLogin();
loadActiveResources().then(() => {
  // 加载资源后初始化删除下拉框
  renderDeleteResourceOptions(activeResources);
  // 绘制物理资源可视化图
  renderResourceVisualization();
});

logoutBtn.addEventListener("click", () => {
  localStorage.removeItem("qos_token");
  localStorage.removeItem("qos_user");
  window.location.href = "../index.html";
});

pathForm.addEventListener("submit", (event) => {
  event.preventDefault();
  setMessage("");

  const businessImagePath = rgbPathInput.value.trim();
  const pointCloudPath = pointCloudPathInput.value.trim();

  if (!businessImagePath || !pointCloudPath) {
    setMessage("请填写 RGB 与点云文件路径", "error");
    return;
  }

  savedPaths = { businessImagePath, pointCloudPath };
  pathSaved = true;
  setMessage("路径已保存，请继续填写资源需求", "success");
});

if (refreshResourceBtn) {
  refreshResourceBtn.addEventListener("click", () => {
    loadActiveResources().then(() => {
      renderResourceVisualization();
    });
  });
}

if (resourceProfileSelect) {
  resourceProfileSelect.addEventListener("change", (event) => {
    // 选择资源后不需要填充到表单，编排时自动使用所有资源
  });
}

resourceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("");

  if (!pathSaved) {
    setMessage("请先保存文件路径", "error");
    return;
  }

  const token = getAuthToken();
  if (!token) {
    setMessage("未检测到登录态，请重新登录", "error");
    return;
  }

  const payload = {
    businessImagePath: savedPaths.businessImagePath,
    pointCloudPath: savedPaths.pointCloudPath,
  };

  try {
    setMessage("后端处理中，请稍候...");
    const orchestrationUrl = `${API_CONFIG.GATEWAY_URL}${API_CONFIG.ENDPOINTS.runOrchestration}`;
    const response = await fetch(orchestrationUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(payload),
    });

    const data = await parseResponse(response);
    if (!response.ok) {
      const message = typeof data === "object" && data?.message ? data.message : "编排失败";
      throw new Error(message);
    }

    resultEl.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
    const content = typeof data === "string" ? data : JSON.stringify(data, null, 2);
    downloadableBlob = new Blob([content], { type: "application/json;charset=utf-8" });
    downloadBtn.disabled = false;
    setMessage("编排完成，可下载结果", "success");

    // 绘制编排结果可视化
    if (data && (data.vnf_node || data.link_path)) {
      renderOrchestrationResult(data);
    }
  } catch (error) {
    resultEl.textContent = "暂无结果";
    downloadableBlob = null;
    downloadBtn.disabled = true;
    setMessage(error.message || "编排失败", "error");
  }
});

downloadBtn.addEventListener("click", () => {
  if (!downloadableBlob) {
    setMessage("当前没有可下载内容", "error");
    return;
  }

  const blobUrl = URL.createObjectURL(downloadableBlob);
  const anchor = document.createElement("a");
  anchor.href = blobUrl;
  anchor.download = `qos-orchestration-result-${Date.now()}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(blobUrl);
});

// ========== 添加节点资源 ==========
const addNodeForm = document.getElementById("add-node-form");
const addNodeMessageEl = document.getElementById("add-node-message");

function setAddNodeMessage(text, type = "") {
  if (!addNodeMessageEl) return;
  addNodeMessageEl.textContent = text;
  addNodeMessageEl.className = `message ${type}`.trim();
}

if (addNodeForm) {
  addNodeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setAddNodeMessage("");

    const cpu = Number(document.getElementById("node-cpu")?.value);
    const memory = Number(document.getElementById("node-memory")?.value);
    const energy_consumption = Number(document.getElementById("node-energy")?.value);
    const vcpu = Number(document.getElementById("node-vcpu")?.value);
    const memory_gb = Number(document.getElementById("node-memory-gb")?.value);
    const storage = Number(document.getElementById("node-storage")?.value);
    const bandwidth = Number(document.getElementById("node-bandwidth")?.value);

    if ([cpu, memory, energy_consumption].some(v => v < 0 || v > 1)) {
      setAddNodeMessage("CPU、内存、能耗必须在0-1之间", "error");
      return;
    }

    const token = getAuthToken();
    if (!token) {
      setAddNodeMessage("未检测到登录态，请重新登录", "error");
      return;
    }

    try {
      const response = await fetch(`${API_CONFIG.GATEWAY_URL}${API_CONFIG.ENDPOINTS.addNode}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ cpu, memory, energy_consumption, vcpu, memory_gb, storage, bandwidth }),
      });

      const data = await parseResponse(response);
      if (!response.ok) {
        throw new Error(data?.message || "添加节点失败");
      }

      setAddNodeMessage(data.message || "节点添加成功", "success");
      addNodeForm.reset();
      // 刷新资源列表并更新可视化
      loadActiveResources().then(() => {
        renderResourceVisualization();
      });
    } catch (error) {
      setAddNodeMessage(error.message || "添加节点失败", "error");
    }
  });
}

// ========== 添加链路资源 ==========
const addLinkForm = document.getElementById("add-link-form");
const addLinkMessageEl = document.getElementById("add-link-message");

function setAddLinkMessage(text, type = "") {
  if (!addLinkMessageEl) return;
  addLinkMessageEl.textContent = text;
  addLinkMessageEl.className = `message ${type}`.trim();
}

if (addLinkForm) {
  addLinkForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setAddLinkMessage("");

    const src_node = Number(document.getElementById("link-src-node")?.value);
    const dst_node = Number(document.getElementById("link-dst-node")?.value);
    const bandwidth = Number(document.getElementById("link-bandwidth")?.value);
    const latency = Number(document.getElementById("link-latency")?.value);
    const path_id = Number(document.getElementById("link-path-id")?.value);

    if (src_node === dst_node) {
      setAddLinkMessage("源节点和目标节点不能相同", "error");
      return;
    }

    const token = getAuthToken();
    if (!token) {
      setAddLinkMessage("未检测到登录态，请重新登录", "error");
      return;
    }

    try {
      const response = await fetch(`${API_CONFIG.GATEWAY_URL}${API_CONFIG.ENDPOINTS.addLink}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ src_node, dst_node, bandwidth, latency, path_id }),
      });

      const data = await parseResponse(response);
      if (!response.ok) {
        throw new Error(data?.message || "添加链路失败");
      }

      setAddLinkMessage(data.message || "链路添加成功", "success");
      addLinkForm.reset();
      // 刷新资源列表并更新可视化
      loadActiveResources().then(() => {
        renderResourceVisualization();
      });
    } catch (error) {
      setAddLinkMessage(error.message || "添加链路失败", "error");
    }
  });
}

// ========== 删除资源 ==========
const deleteResourceForm = document.getElementById("delete-resource-form");
const deleteResourceSelect = document.getElementById("delete-resource-select");
const deleteResourceMessageEl = document.getElementById("delete-resource-message");

function setDeleteResourceMessage(text, type = "") {
  if (!deleteResourceMessageEl) return;
  deleteResourceMessageEl.textContent = text;
  deleteResourceMessageEl.className = `message ${type}`.trim();
}

// 渲染删除资源下拉框
function renderDeleteResourceOptions(resources) {
  if (!deleteResourceSelect) return;

  deleteResourceSelect.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "请选择要删除的资源";
  deleteResourceSelect.appendChild(placeholder);

  for (const resource of resources) {
    const option = document.createElement("option");
    option.value = String(resource?.resource_id || resource?.id || "");
    option.textContent = getResourceLabel(resource);
    deleteResourceSelect.appendChild(option);
  }
}

// 刷新时也更新删除下拉框
function refreshAllResourceOptions() {
  loadActiveResources().then(() => {
    renderDeleteResourceOptions(activeResources);
  });
}

if (deleteResourceForm) {
  deleteResourceForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setDeleteResourceMessage("");

    const resourceId = deleteResourceSelect?.value;
    if (!resourceId) {
      setDeleteResourceMessage("请先选择要删除的资源", "error");
      return;
    }

    const token = getAuthToken();
    if (!token) {
      setDeleteResourceMessage("未检测到登录态，请重新登录", "error");
      return;
    }

    const confirmed = confirm(`确定要删除资源 ${resourceId} 吗？此操作不可恢复。`);
    if (!confirmed) {
      return;
    }

    try {
      setDeleteResourceMessage("正在删除资源...");
      const response = await fetch(
        `${API_CONFIG.GATEWAY_URL}${API_CONFIG.ENDPOINTS.deleteResource}${resourceId}`,
        {
          method: "DELETE",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
        }
      );

      const data = await parseResponse(response);
      if (!response.ok) {
        throw new Error(data?.message || "删除资源失败");
      }

      setDeleteResourceMessage(data.message || "资源删除成功", "success");
      deleteResourceSelect.value = "";
      // 刷新资源列表并更新可视化
      loadActiveResources().then(() => {
        renderResourceVisualization();
      });
    } catch (error) {
      setDeleteResourceMessage(error.message || "删除资源失败", "error");
    }
  });
}

// ========== 编排结果可视化 ==========
const resultCanvas = document.getElementById("result-canvas");
const resultVisualizationStatusEl = document.getElementById("result-visualization-status");

function setResultVisualizationMessage(text, type = "") {
  if (!resultVisualizationStatusEl) return;
  resultVisualizationStatusEl.textContent = text;
  resultVisualizationStatusEl.className = `message ${type}`.trim();
}

function renderOrchestrationResult(result) {
  if (!resultCanvas) {
    return;
  }

  const ctx = resultCanvas.getContext("2d");
  const container = document.getElementById("result-visualization-container");

  // 设置 canvas 尺寸
  const rect = container.getBoundingClientRect();
  resultCanvas.width = rect.width;
  resultCanvas.height = 300;

  // 清空画布
  ctx.clearRect(0, 0, resultCanvas.width, resultCanvas.height);

  // 获取编排结果中的节点和链路
  const vnfNodes = result.vnf_node || [];
  const linkPaths = result.link_path || [];
  const qosOk = result.qos_ok !== undefined ? result.qos_ok : true;
  const resourceOk = result.resource_ok !== undefined ? result.resource_ok : true;

  // 从 activeResources 获取物理节点信息
  const nodes = [];
  const nodeMap = {};

  for (const resource of activeResources) {
    const state = getResourceState(resource);
    const type = resource?.resource_type || state?.type;

    if (type === "node" || type === "compute") {
      let nodeId = 0;
      if (resource?.resource_id) {
        const match = resource.resource_id.match(/(\d+)$/);
        if (match) {
          nodeId = parseInt(match[1], 10);
        }
      }

      const isSelected = vnfNodes.includes(nodeId);
      nodes.push({
        id: nodeId,
        name: resource?.resource_name || `节点 ${nodeId}`,
        vcpu: state?.vcpu || 0,
        memory_gb: state?.memory_gb || 0,
        isSelected: isSelected,
        isVNF: isSelected,
      });
      nodeMap[nodeId] = nodes[nodes.length - 1];
    }
  }

  // 获取链路信息
  const links = [];
  for (const resource of activeResources) {
    const state = getResourceState(resource);
    const type = resource?.resource_type || state?.type;

    if (type === "link" || type === "edge" || type === "network") {
      const src = state?.src ?? 0;
      const dst = state?.dst ?? 0;

      // 检查这条链路是否被选中
      let isSelected = false;
      for (const pathIdx of linkPaths) {
        // link-0 对应索引0，link-1 对应索引1，以此类推
        const linkMatch = resource?.resource_id?.match(/link-(\d+)/);
        if (linkMatch && parseInt(linkMatch[1]) === pathIdx) {
          isSelected = true;
          break;
        }
      }

      if (isSelected) {
        links.push({
          src: src,
          dst: dst,
          bandwidth: state?.bandwidth || 0,
          isSelected: isSelected,
        });
      }
    }
  }

  // 排序节点
  nodes.sort((a, b) => a.id - b.id);

  if (nodes.length === 0) {
    ctx.fillStyle = "#5d6470";
    ctx.font = "14px Arial";
    ctx.textAlign = "center";
    ctx.fillText("暂无物理资源", resultCanvas.width / 2, resultCanvas.height / 2);
    return;
  }

  // 节点布局 - 圆形布局
  const centerX = resultCanvas.width / 2;
  const centerY = resultCanvas.height / 2;
  const radius = Math.min(resultCanvas.width, resultCanvas.height) * 0.35;

  const nodePositions = {};

  for (let i = 0; i < nodes.length; i++) {
    const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
    nodePositions[nodes[i].id] = {
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
    };
  }

  // 绘制所有链路（灰色，作为背景）
  ctx.strokeStyle = "#e2e8f0";
  ctx.lineWidth = 2;

  for (const resource of activeResources) {
    const state = getResourceState(resource);
    const type = resource?.resource_type || state?.type;

    if (type === "link" || type === "edge" || type === "network") {
      const src = state?.src ?? 0;
      const dst = state?.dst ?? 0;
      const srcPos = nodePositions[src];
      const dstPos = nodePositions[dst];

      if (srcPos && dstPos) {
        ctx.beginPath();
        ctx.moveTo(srcPos.x, srcPos.y);
        ctx.lineTo(dstPos.x, dstPos.y);
        ctx.stroke();
      }
    }
  }

  // 绘制选中的链路
  const linkColor = (qosOk && resourceOk) ? "#22c55e" : "#ef4444";
  ctx.strokeStyle = linkColor;
  ctx.lineWidth = 3;

  for (const link of links) {
    const srcPos = nodePositions[link.src];
    const dstPos = nodePositions[link.dst];

    if (!srcPos || !dstPos) continue;

    // 绘制连线
    ctx.beginPath();
    ctx.moveTo(srcPos.x, srcPos.y);
    ctx.lineTo(dstPos.x, dstPos.y);
    ctx.stroke();

    // 绘制带宽标签
    const midX = (srcPos.x + dstPos.x) / 2;
    const midY = (srcPos.y + dstPos.y) / 2;
    const bandwidthText = `带宽: ${link.bandwidth}`;
    const textMetrics = ctx.measureText(bandwidthText);
    const padding = 3;

    ctx.fillStyle = "#fff";
    ctx.fillRect(midX - textMetrics.width / 2 - padding, midY - 8 - padding, textMetrics.width + padding * 2, 16 + padding);
    ctx.fillStyle = linkColor;
    ctx.font = "10px Arial";
    ctx.textAlign = "center";
    ctx.fillText(bandwidthText, midX, midY);
  }

  // 绘制节点
  for (const node of nodes) {
    const pos = nodePositions[node.id];
    if (!pos) continue;

    // 确定节点颜色
    let nodeColor = "#94a3b8"; // 默认灰色 - 未选中
    let borderColor = "#64748b";

    if (node.isSelected) {
      if (qosOk && resourceOk) {
        nodeColor = "#22c55e"; // 绿色 - 满足要求
        borderColor = "#16a34a";
      } else {
        nodeColor = "#ef4444"; // 红色 - 不满足要求
        borderColor = "#dc2626";
      }
    }

    // 绘制圆圈
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, 25, 0, 2 * Math.PI);
    ctx.fillStyle = nodeColor;
    ctx.fill();
    ctx.strokeStyle = borderColor;
    ctx.lineWidth = 2;
    ctx.stroke();

    // 绘制节点名称
    ctx.fillStyle = "#111111";
    ctx.font = "bold 12px Arial";
    ctx.textAlign = "center";
    ctx.fillText(node.name, pos.x, pos.y - 32);

    // 绘制 VCPU 和内存
    ctx.fillStyle = "#5d6470";
    ctx.font = "10px Arial";
    ctx.fillText(`vCPU: ${node.vcpu}`, pos.x, pos.y + 40);
    ctx.fillText(`内存: ${node.memory_gb}GB`, pos.x, pos.y + 52);
  }

  // 显示状态信息
  const statusText = `节点: ${vnfNodes.length}个, 链路: ${linkPaths.length}条 | QoS: ${qosOk ? '满足' : '不满足'} | 资源: ${resourceOk ? '满足' : '不满足'}`;
  setResultVisualizationMessage(statusText, (qosOk && resourceOk) ? "success" : "error");
}
