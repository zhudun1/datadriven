const step1Form = document.getElementById("step1-form");
const step2Form = document.getElementById("step2-form");
const messageEl = document.getElementById("orchestration-message");
const resultEl = document.getElementById("orchestration-result");
const downloadBtn = document.getElementById("download-btn");
const logoutBtn = document.getElementById("logout-btn");
const resourceSection = document.getElementById("resource-consumption-section");
const consumptionSummary = document.getElementById("consumption-summary");

let step1Done = false;
let downloadableBlob = null;
let cpuChart = null;
let memoryChart = null;
let bandwidthChart = null;

function setMessage(text, type = "") {
  messageEl.textContent = text;
  messageEl.className = `message ${type}`.trim();
}

function requireLogin() {
  const token = localStorage.getItem("qos_token");
  const user = localStorage.getItem("qos_user");
  if (!token && !user) {
    window.location.href = "./index.html";
  }
}

requireLogin();

logoutBtn.addEventListener("click", () => {
  localStorage.removeItem("qos_token");
  localStorage.removeItem("qos_user");
  window.location.href = "./index.html";
});

// 模型选择切换
const modelTypeSelect = document.getElementById("model-type");
const cfmInputs = document.getElementById("cfm-inputs");
const jigsawInputs = document.getElementById("jigsaw-inputs");

if (modelTypeSelect) {
  modelTypeSelect.addEventListener("change", () => {
    const type = modelTypeSelect.value;
    if (type === "CFM") {
      if (cfmInputs) cfmInputs.style.display = "block";
      if (jigsawInputs) jigsawInputs.style.display = "none";
    } else {
      if (cfmInputs) cfmInputs.style.display = "none";
      if (jigsawInputs) jigsawInputs.style.display = "block";
    }
  });
}

step1Form.addEventListener("submit", (event) => {
  event.preventDefault();
  setMessage("");

  console.log("Step1 submitted, checking form...");
  const modelType = modelTypeSelect ? modelTypeSelect.value : "CFM";
  console.log("Model type:", modelType);
  let dataPath1, dataPath2;

  if (modelType === "CFM") {
    dataPath1 = document.getElementById("business-image-path")?.value?.trim();
    dataPath2 = document.getElementById("point-cloud-path")?.value?.trim();
    if (!dataPath1 || !dataPath2) {
      setMessage("请填写图片和点云路径", "error");
      return;
    }
  } else {
    dataPath1 = document.getElementById("video-path")?.value?.trim()?.replace(/"/g, "") || "";
    dataPath2 = document.getElementById("aux-image-path")?.value?.trim()?.replace(/"/g, "") || "";
    if (!dataPath1 && !dataPath2) {
      setMessage("请填写视频或图像路径", "error");
      return;
    }

    console.log("Step1 data validated, videoPath:", dataPath1, "auxImagePath:", dataPath2);
  }

  step1Done = true;
  setMessage("步骤 1 已完成，请继续填写资源需求", "success");
});

// 解析资源消耗数据并生成可视化
function displayResourceConsumption(result) {
  const resourceConsumption = result.resource_consumption;
  const linkConsumption = result.link_consumption;

  if (!resourceConsumption || resourceConsumption.length === 0) {
    resourceSection.style.display = "none";
    return;
  }

  resourceSection.style.display = "block";

  // 生成汇总信息
  const totalCpu = resourceConsumption.reduce((sum, item) => sum + item.cpu_used, 0);
  const totalMemory = resourceConsumption.reduce((sum, item) => sum + item.memory_used, 0);
  const nodeNames = resourceConsumption.map(item => item.node_name).join(", ");

  consumptionSummary.innerHTML = `
    <div class="summary-item">
      <span class="summary-label">部署节点:</span>
      <span class="summary-value">${nodeNames}</span>
    </div>
    <div class="summary-item">
      <span class="summary-label">总 CPU 消耗:</span>
      <span class="summary-value">${totalCpu.toFixed(4)} 核</span>
    </div>
    <div class="summary-item">
      <span class="summary-label">总内存消耗:</span>
      <span class="summary-value">${totalMemory.toFixed(4)} GB</span>
    </div>
    ${linkConsumption && linkConsumption.length > 0 ? `
    <div class="summary-item">
      <span class="summary-label">链路带宽消耗:</span>
      <span class="summary-value">${linkConsumption[0].bandwidth_used.toFixed(4)} Mbps (延迟: ${linkConsumption[0].latency.toFixed(2)} ms)</span>
    </div>
    ` : ''}
  `;

  // 销毁旧图表
  if (cpuChart) { cpuChart.destroy(); cpuChart = null; }
  if (memoryChart) { memoryChart.destroy(); memoryChart = null; }
  if (bandwidthChart) { bandwidthChart.destroy(); bandwidthChart = null; }

  // 生成节点标签
  const labels = resourceConsumption.map((item, idx) => `VNF-${idx} (${item.node_name})`);
  const cpuData = resourceConsumption.map(item => item.cpu_used);
  const memData = resourceConsumption.map(item => item.memory_used);

  // CPU 消耗柱状图
  const cpuCtx = document.getElementById("cpu-chart").getContext("2d");
  cpuChart = new Chart(cpuCtx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        label: "CPU 消耗 (核)",
        data: cpuData,
        backgroundColor: "rgba(59, 130, 246, 0.7)",
        borderColor: "rgba(59, 130, 246, 1)",
        borderWidth: 1
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: true }
      },
      scales: {
        y: {
          beginAtZero: true,
          title: { display: true, text: "CPU (核)" }
        }
      }
    }
  });

  // 内存消耗柱状图
  const memCtx = document.getElementById("memory-chart").getContext("2d");
  memoryChart = new Chart(memCtx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        label: "内存消耗 (GB)",
        data: memData,
        backgroundColor: "rgba(16, 185, 129, 0.7)",
        borderColor: "rgba(16, 185, 129, 1)",
        borderWidth: 1
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: true }
      },
      scales: {
        y: {
          beginAtZero: true,
          title: { display: true, text: "内存 (GB)" }
        }
      }
    }
  });

  // 链路带宽消耗（如果有）
  const bwCtx = document.getElementById("bandwidth-chart").getContext("2d");
  if (linkConsumption && linkConsumption.length > 0) {
    const linkLabels = linkConsumption.map(item => `节点${item.src_node} → 节点${item.dst_node}`);
    const bwData = linkConsumption.map(item => item.bandwidth_used);
    const latData = linkConsumption.map(item => item.latency);

    bandwidthChart = new Chart(bwCtx, {
      type: "bar",
      data: {
        labels: linkLabels,
        datasets: [
          {
            label: "带宽消耗 (Mbps)",
            data: bwData,
            backgroundColor: "rgba(249, 115, 22, 0.7)",
            borderColor: "rgba(249, 115, 22, 1)",
            borderWidth: 1,
            yAxisID: "y"
          },
          {
            label: "延迟 (ms)",
            data: latData,
            type: "line",
            borderColor: "rgba(239, 68, 68, 1)",
            backgroundColor: "rgba(239, 68, 68, 0.2)",
            fill: true,
            yAxisID: "y1"
          }
        ]
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: true }
        },
        scales: {
          y: {
            type: "linear",
            position: "left",
            title: { display: true, text: "带宽 (Mbps)" }
          },
          y1: {
            type: "linear",
            position: "right",
            title: { display: true, text: "延迟 (ms)" },
            grid: { drawOnChartArea: false }
          }
        }
      }
    });
  } else {
    // 无链路消耗时显示空图表
    bandwidthChart = new Chart(bwCtx, {
      type: "bar",
      data: {
        labels: ["无链路消耗"],
        datasets: [{
          label: "带宽消耗",
          data: [0],
          backgroundColor: "rgba(156, 163, 175, 0.5)"
        }]
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false }
        }
      }
    });
  }
}

step2Form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("");

  console.log("Step2 submitted, step1Done:", step1Done);

  if (!step1Done) {
    setMessage("请先完成步骤 1", "error");
    return;
  }

  const modelType = modelTypeSelect ? modelTypeSelect.value : "CFM";
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
    model_type: modelType,
    resourceRequest: resources
  };

  if (modelType === "CFM") {
    requestData.businessImagePath = document.getElementById("rgb-path").value.trim();
    requestData.pointCloudPath = document.getElementById("pcd-path").value.trim();
  } else {
    requestData.videoPath = document.getElementById("video-path").value.trim().replace(/"/g, "");
    requestData.auxiliaryImagePath = document.getElementById("aux-image-path").value.trim().replace(/"/g, "");
  }

  try {
    setMessage("后端处理中，请稍候...");
    console.log("Sending request to backend...");
    console.log("Request data:", JSON.stringify(requestData));

    const token = localStorage.getItem("qos_token");
    console.log("Token:", token);
    const response = await fetch("http://localhost:8001/pipeline", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": token ? `Bearer ${token}` : ""
      },
      body: JSON.stringify(requestData)
    });

    console.log("Response status:", response.status);
    const result = await response.json();
    console.log("Result:", result);

    if (result.status === "success" || result.status === "timeout") {
      resultEl.textContent = typeof result === "string" ? result : JSON.stringify(result, null, 2);
      setMessage(result.status === "timeout" ? "处理超时" : "编排完成", "success");
    } else {
      setMessage(result.message || "编排失败", "error");
    }
  } catch (error) {
    setMessage(error.message || "请求失败", "error");
    resultEl.textContent = "暂无结果";
  }

  downloadBtn.disabled = true;
});

downloadBtn.addEventListener("click", () => {
  const downloadUrl = downloadBtn.dataset.url;

  if (downloadUrl) {
    window.open(downloadUrl, "_blank", "noopener");
    return;
  }

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