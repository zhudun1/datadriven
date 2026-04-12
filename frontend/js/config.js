export const API_CONFIG = {
  // 用户中心（登录/注册）
  BASE_URL: "http://localhost:8003",
  // API 网关（资源管理、编排请求）
  GATEWAY_URL: "http://localhost:8001",
  FRONTEND_ONLY: false,
  ENABLE_LOCAL_FALLBACK: false,
  ENDPOINTS: {
    login: "/login",
    register: "/register",
    sendCode: "/send-code",
    // 编排走网关
    runOrchestration: "/pipeline",
    activeResources: "/resources/active",
    addNode: "/resources/nodes",
    addLink: "/resources/links",
    deleteResource: "/resources/",  // DELETE /resources/{resource_id}
  },
};
