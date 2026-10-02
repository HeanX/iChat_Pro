/**
 * Desktop app mode resolution (P4 T14 review: normalised origin comparison,
 * explicit dev gating). Pure module - Node testable.
 */
function normalizeOrigin(raw) {
  try {
    const u = new URL(String(raw).trim());
    if (u.protocol !== "https:" && u.protocol !== "http:") return null;
    let port = u.port;
    if ((u.protocol === "https:" && port === "443") || (u.protocol === "http:" && port === "80")) {
      port = "";
    }
    return `${u.protocol}//${u.hostname.toLowerCase()}${port ? ":" + port : ""}`;
  } catch {
    return null;
  }
}

function resolveAppConfig(env, argv) {
  const raw = String(env.ICHAT_SERVER_URL || "").trim();
  const cloudOrigin = normalizeOrigin(raw);
  const isDev = Array.isArray(argv) && (argv.includes("--dev") || env.ICHAT_DEV === "1");

  if (cloudOrigin) {
    // Production contract: remote service addresses must use HTTPS; plain
    // HTTP is accepted only for loopback targets (local testing).
    let hostname = "";
    try { hostname = new URL(cloudOrigin).hostname; } catch (e) { hostname = ""; }
    if (cloudOrigin.startsWith("http://") && !["localhost", "127.0.0.1", "::1"].includes(hostname)) {
      return {
        mode: "unconfigured",
        origin: null,
        message: "远程服务地址必须使用 HTTPS（当前为 HTTP）：" + cloudOrigin,
      };
    }
    return { mode: "cloud", origin: cloudOrigin, message: null };
  }
  if (isDev) {
    const host = env.ICHAT_HOST || "127.0.0.1";
    const port = env.ICHAT_PORT || "8000";
    return { mode: "dev", origin: `http://${host}:${port}`, message: null };
  }
  if (raw && !cloudOrigin) {
    return {
      mode: "unconfigured",
      origin: null,
      message: "ICHAT_SERVER_URL 不是合法的 HTTP(S) 地址：" + raw,
    };
  }
  return {
    mode: "unconfigured",
    origin: null,
    message: "未配置服务地址：请设置 ICHAT_SERVER_URL（例如 https://sub.example.com:8443），或使用 --dev 启动本地开发模式。",
  };
}

module.exports = { normalizeOrigin, resolveAppConfig };
