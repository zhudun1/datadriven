import { login } from "./api.js";
import { isEmail } from "./validators.js";

const globalMessage = document.getElementById("global-message");
const loginForm = document.getElementById("login-form");

function setMessage(text, type = "") {
  if (!globalMessage) {
    return;
  }
  globalMessage.textContent = text;
  globalMessage.className = `message ${type}`.trim();
}

function bindPasswordToggle() {
  document.querySelectorAll("[data-toggle-password]").forEach((button) => {
    button.addEventListener("click", () => {
      const targetId = button.getAttribute("data-toggle-password");
      const input = targetId ? document.getElementById(targetId) : null;
      if (!input) {
        return;
      }

      const visible = input.type === "text";
      input.type = visible ? "password" : "text";
      button.textContent = visible ? "显示" : "隐藏";
    });
  });
}

bindPasswordToggle();

if (loginForm) {
  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setMessage("");

    const email = document.getElementById("login-email")?.value.trim() || "";
    const password = document.getElementById("login-password")?.value || "";

    if (!isEmail(email)) {
      setMessage("请输入有效邮箱地址", "error");
      return;
    }

    if (!password) {
      setMessage("请输入密码", "error");
      return;
    }

    try {
      const response = await login({ username: email, password });
      if (response?.token) {
        localStorage.setItem("qos_token", response.token);
      }
      localStorage.setItem("qos_user", JSON.stringify(response?.user || { email }));
      window.location.href = "./sandbox/index.html";
    } catch (error) {
      setMessage(error.message || "登录失败", "error");
    }
  });
}
