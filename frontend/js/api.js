import { API_CONFIG } from "./config.js";

const LS_USERS_KEY = "qos_local_users";
const LS_SMS_CODES_KEY = "qos_local_sms_codes";

function parseJSON(value, fallback) {
  try {
    return value ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
}

function loadUsers() {
  return parseJSON(localStorage.getItem(LS_USERS_KEY), []);
}

function saveUsers(users) {
  localStorage.setItem(LS_USERS_KEY, JSON.stringify(users));
}

function loadSmsCodes() {
  return parseJSON(localStorage.getItem(LS_SMS_CODES_KEY), {});
}

function saveSmsCodes(codes) {
  localStorage.setItem(LS_SMS_CODES_KEY, JSON.stringify(codes));
}

async function request(path, options = {}) {
  const token = localStorage.getItem("qos_token");
  const headers = {
    ...(options.headers || {}),
  };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  // 编排走网关
  const baseUrl = options.useGateway ? API_CONFIG.GATEWAY_URL : API_CONFIG.BASE_URL;

  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers,
  });

  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const data = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message = typeof data === "object" && data?.message ? data.message : "请求失败";
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }

  return data;
}

function shouldUseLocalFallback(error) {
  if (!API_CONFIG.ENABLE_LOCAL_FALLBACK) {
    return false;
  }
  return error instanceof TypeError || error?.status === 404;
}

function isFrontendOnlyMode() {
  return Boolean(API_CONFIG.FRONTEND_ONLY);
}

async function localLogin(payload) {
  const users = loadUsers();
  const user = users.find(
    (item) =>
      (item.email === payload.username || item.phone === payload.username) &&
      item.password === payload.password,
  );

  if (!user) {
    throw new Error("用户名或密码错误");
  }

  return {
    token: `local-token-${Date.now()}`,
    user: {
      name: user.name,
      phone: user.phone,
      email: user.email,
    },
  };
}

async function localRegister(payload) {
  const users = loadUsers();
  const exists = users.some((item) => item.phone === payload.phone || item.email === payload.email);
  if (exists) {
    throw new Error("该手机号或邮箱已注册");
  }

  const codes = loadSmsCodes();
  if (!codes[payload.phone] || codes[payload.phone] !== payload.code) {
    throw new Error("验证码无效或已过期");
  }

  users.push({
    name: payload.name,
    phone: payload.phone,
    email: payload.email,
    password: payload.password,
    createdAt: Date.now(),
  });
  saveUsers(users);

  delete codes[payload.phone];
  saveSmsCodes(codes);

  return { message: "注册成功" };
}

async function localSendCode(payload) {
  const codes = loadSmsCodes();
  codes[payload.phone] = "123456";
  saveSmsCodes(codes);
  return { message: "验证码已发送", debugCode: "123456" };
}

async function localRunOrchestration(formData) {
  const image = formData.get("businessImage");
  const resourceRaw = formData.get("resourceRequest");
  const resources = typeof resourceRaw === "string" ? parseJSON(resourceRaw, {}) : {};

  return {
    mode: "local-fallback",
    status: "success",
    orchestratedAt: new Date().toISOString(),
    imageName: image?.name || "unknown",
    resourcePlan: {
      vcpu: resources.vcpu ?? null,
      memory: resources.memory ?? null,
      storage: resources.storage ?? null,
      bandwidth: resources.bandwidth ?? null,
      note: resources.note || "",
    },
    suggestion: "这是本地演示结果。接入真实后端后将返回正式编排方案。",
  };
}

export async function login(payload) {
  if (isFrontendOnlyMode()) {
    return localLogin(payload);
  }

  try {
    return await request(API_CONFIG.ENDPOINTS.login, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    if (!shouldUseLocalFallback(error)) {
      throw error;
    }
    return localLogin(payload);
  }
}

export async function register(payload) {
  if (isFrontendOnlyMode()) {
    return localRegister(payload);
  }

  try {
    return await request(API_CONFIG.ENDPOINTS.register, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    if (!shouldUseLocalFallback(error)) {
      throw error;
    }
    return localRegister(payload);
  }
}

export async function sendCode(payload) {
  if (isFrontendOnlyMode()) {
    return localSendCode(payload);
  }

  try {
    return await request(API_CONFIG.ENDPOINTS.sendCode, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    if (!shouldUseLocalFallback(error)) {
      throw error;
    }
    return localSendCode(payload);
  }
}

export async function runOrchestration(formData) {
  if (isFrontendOnlyMode()) {
    return localRunOrchestration(formData);
  }

  // 编排请求走网关
  const gatewayUrl = API_CONFIG.GATEWAY_URL + API_CONFIG.ENDPOINTS.runOrchestration;

  try {
    const token = localStorage.getItem("qos_token");
    const headers = {};
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }

    return await request(API_CONFIG.ENDPOINTS.runOrchestration, {
      method: "POST",
      body: formData,
      useGateway: true,
    });
  } catch (error) {
    if (!shouldUseLocalFallback(error)) {
      throw error;
    }
    return localRunOrchestration(formData);
  }
}
