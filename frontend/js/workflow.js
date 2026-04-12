import { runOrchestration } from "./api.js";

const step1Form = document.getElementById("step1-form");
const step2Form = document.getElementById("step2-form");
const imageInput = document.getElementById("business-image");
const messageEl = document.getElementById("orchestration-message");
const resultEl = document.getElementById("orchestration-result");
const downloadBtn = document.getElementById("download-btn");
const logoutBtn = document.getElementById("logout-btn");

let step1Done = false;
let savedImageFile = null;
let downloadableBlob = null;

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

step1Form.addEventListener("submit", (event) => {
  event.preventDefault();
  setMessage("");

  const file = imageInput.files?.[0];
  if (!file) {
    setMessage("请先上传业务图片", "error");
    return;
  }

  savedImageFile = file;
  step1Done = true;
  setMessage("步骤 1 已完成，请继续填写资源需求", "success");
});

step2Form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("");

  if (!step1Done || !savedImageFile) {
    setMessage("请先完成步骤 1 的图片上传", "error");
    return;
  }

  const vcpu = Number(document.getElementById("vcpu").value);
  const memory = Number(document.getElementById("memory").value);
  const storage = Number(document.getElementById("storage").value);
  const bandwidth = Number(document.getElementById("bandwidth").value);
  const note = document.getElementById("resource-note").value.trim();

  if ([vcpu, memory, storage, bandwidth].some((value) => Number.isNaN(value) || value <= 0)) {
    setMessage("资源参数必须是大于 0 的数字", "error");
    return;
  }

  const resources = { vcpu, memory, storage, bandwidth, note };
  const formData = new FormData();
  formData.append("businessImage", savedImageFile);
  formData.append("resourceRequest", JSON.stringify(resources));

  try {
    setMessage("后端处理中，请稍候...");
    const response = await runOrchestration(formData);
    resultEl.textContent = typeof response === "string" ? response : JSON.stringify(response, null, 2);

    if (typeof response === "object" && response?.downloadUrl) {
      downloadableBlob = null;
      downloadBtn.disabled = false;
      downloadBtn.dataset.url = response.downloadUrl;
    } else {
      const content = typeof response === "string" ? response : JSON.stringify(response, null, 2);
      downloadableBlob = new Blob([content], { type: "application/json;charset=utf-8" });
      downloadBtn.dataset.url = "";
      downloadBtn.disabled = false;
    }

    setMessage("编排完成，可下载结果", "success");
  } catch (error) {
    setMessage(error.message || "编排失败", "error");
    resultEl.textContent = "暂无结果";
    downloadBtn.disabled = true;
  }
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