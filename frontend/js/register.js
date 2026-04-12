import { register } from "./api.js";
import { isEmail } from "./validators.js";

const registerForm = document.getElementById("register-form");
const registerMessage = document.getElementById("register-message");

function setMessage(text, type = "") {
  if (!registerMessage) {
    return;
  }
  registerMessage.textContent = text;
  registerMessage.className = `message ${type}`.trim();
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

if (registerForm) {
  registerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setMessage("");

    const email = document.getElementById("register-email")?.value.trim() || "";
    const password = document.getElementById("register-password")?.value || "";
    const confirmPassword = document.getElementById("register-confirm-password")?.value || "";

    if (!isEmail(email)) {
      setMessage("请输入有效邮箱地址", "error");
      return;
    }

    if (!password) {
      setMessage("请输入密码", "error");
      return;
    }

    if (password !== confirmPassword) {
      setMessage("两次输入的密码不一致", "error");
      return;
    }

    try {
      await register({
        name: email.split("@")[0],
        email,
        password,
      });

      setMessage("注册成功，正在跳转登录页...", "success");
      setTimeout(() => {
        window.location.href = "./index.html";
      }, 900);
    } catch (error) {
      setMessage(error.message || "注册失败", "error");
    }
  });
}
