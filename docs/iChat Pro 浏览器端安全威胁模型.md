# iChat Pro 浏览器端安全威胁模型

> 版本 1.0 — 2026 年 6 月 4 日
> 覆盖范围：浏览器端私钥存储、E2EE 执行环境、第三方脚本接口

---

## 1. 资产清单

| 资产 | 存储位置 | 敏感度 | 泄露后果 |
| --- | --- | --- | --- |
| 用户身份私钥 (ECDH P-256) | `localStorage` (`ichat_identity_key:{userId}`) | **极高** | 攻击者可解密该用户所有历史及未来消息 |
| 对端公钥信任记录 | `localStorage` (`ichat_peer_identity:{userId}`) | **高** | 可被篡改以实现中间人攻击 |
| 会话派生密钥 (AES-256-GCM) | 内存（不持久化） | **高** | 泄露后可解密当前会话消息 |
| 解密后的明文消息 | 内存/DOM | **高** | 泄露聊天内容 |
| CSRF Token | Cookie | **中** | 可伪造用户请求 |
| 主题偏好 | `localStorage` (`ichat-theme`) | **低** | 无安全影响 |

---

## 2. 威胁模型

### 2.1 威胁角色

| 角色 | 能力 | 风险等级 |
| --- | --- | --- |
| **XSS 攻击者** | 在页面上下文中执行任意 JavaScript | **严重** |
| **供应链攻击者** | 控制 CDN 或第三方脚本内容 | **严重** |
| **物理访问攻击者** | 直接访问用户设备/浏览器 | **高** |
| **网络中间人** | 拦截/篡改 HTTP 流量 | **中**（HTTPS 下缓解） |
| **恶意浏览器扩展** | 访问页面 DOM 和 localStorage | **中** |

### 2.2 攻击向量与缓解措施

#### 2.2.1 XSS → localStorage 私钥窃取

**攻击路径：**
1. 攻击者通过存储型/反射型 XSS 在页面注入恶意脚本
2. 恶意脚本读取 `localStorage.getItem('ichat_identity_key:{userId}')`
3. 私钥被外传至攻击者控制的服务器
4. 攻击者使用私钥解密该用户所有历史及未来消息

**当前状态：**
- 私钥以 JWK 格式明文存储在 `localStorage` 中
- `localStorage` 对同源所有 JavaScript 完全可读
- 无 CSP 头限制脚本执行

**缓解措施：**
- [ ] 部署严格的 Content-Security-Policy (CSP)，禁止 inline script 和未授权外部脚本
- [ ] 对所有用户输入进行服务端 HTML 实体编码（Django 模板默认已做）
- [ ] 考虑将私钥迁移至 `IndexedDB` + `Web Crypto non-extractable` 密钥（长期）
- [ ] 实施 Trusted Types 以防范 DOM-based XSS

#### 2.2.2 CDN 供应链攻击

**攻击路径：**
1. CDN 提供商被入侵，或攻击者发布恶意 npm 版本
2. 恶意脚本被注入 `tailwindcss` 或 `lucide` 的加载链
3. 恶意脚本读取 `localStorage` 或拦截 `Web Crypto API` 调用
4. 私钥或明文消息被外传

**当前状态：**
- `tailwindcss` 从 `cdn.tailwindcss.com` 加载（无 SRI）
- `lucide` 已固定版本 `0.462.0` 并添加 SRI hash
- 无 CSP 限制脚本来源

**缓解措施：**
- [x] `lucide` 已使用 SRI + 固定版本
- [ ] Tailwind CSS Play CDN 无法添加 SRI（内容动态变化）；建议生产环境使用 Tailwind CLI 构建静态 CSS
- [ ] 添加 CSP `script-src` 白名单
- [ ] 定期审查 npm 依赖的 CVE 公告

#### 2.2.3 物理访问 → 浏览器密钥提取

**攻击路径：**
1. 攻击者获得设备的物理访问权限
2. 打开浏览器开发者工具，在 Console 中执行 `localStorage.getItem(...)`
3. 或导出浏览器配置文件

**缓解措施：**
- [ ] 在部署文档中说明设备加密和锁屏策略
- [ ] 为用户提供"退出时清除密钥"选项（考虑到聊天可用性，默认不开启）
- [x] 密钥导出/备份功能已通过文件下载实现，用户自行保管备份文件安全

#### 2.2.4 中间人攻击 → 公钥替换

**攻击路径：**
1. 攻击者拦截客户端到服务器的 HTTPS 请求
2. 替换 `/api/keys/{userId}/` 返回的对端公钥为攻击者公钥
3. 客户端使用攻击者公钥加密消息
4. 攻击者解密后重新加密发送给真正的接收方

**缓解措施：**
- [ ] HTTPS 强制（生产环境必须配置 TLS）
- [ ] 公钥固定（HPKP，已废弃）或 Certificate Transparency 监控
- [x] 首次使用信任 (TOFU) + 密钥指纹验证：客户端在 `rememberPeerIdentity()` 中记录对端公钥指纹，密钥变更时抛出 `peer_key_changed` 错误并阻止发送
- [ ] 考虑提供带外指纹验证通道（如二维码扫码验证）

#### 2.2.5 恶意浏览器扩展

**攻击路径：**
1. 用户安装了具有广泛权限的恶意扩展
2. 扩展读取页面 DOM 或拦截 Web Crypto API
3. 明文消息或密钥材料被窃取

**缓解措施：**
- [ ] 部署 CSP `script-src` 限制（无法完全防御扩展，但可提高门槛）
- [ ] 在用户文档中提醒浏览器扩展风险

---

## 3. 密钥生命周期安全

| 阶段 | 安全措施 | 当前状态 |
| --- | --- | --- |
| **生成** | `window.crypto.subtle.generateKey`（真实随机源） | ✅ 已实现 |
| **存储** | `localStorage` 明文 JWK | ⚠️ 需改善（受 XSS 威胁） |
| **使用** | `window.crypto.subtle.importKey` + 内存中操作 | ✅ 已实现 |
| **轮换** | 上传新公钥 → `key_version` 递增 | ✅ 已实现 |
| **备份** | JSON 文件下载（含明文私钥） | ⚠️ 用户需自行保管 |
| **导入** | JSON 文件上传 + 密钥材料一致性校验 | ✅ 已实现（含公私钥匹配验证） |
| **销毁** | 清除 `localStorage` 对应键 | ⚠️ 无自动过期机制 |

---

## 4. 风险矩阵

| 风险 | 可能性 | 影响 | 风险等级 | 优先级 |
| --- | --- | --- | --- | --- |
| XSS → 私钥窃取 | 中 | 极高 | **严重** | P0 |
| CDN 供应链 → 密钥窃取 | 低 | 极高 | **高** | P1 |
| 物理访问 → 密钥提取 | 低 | 高 | **中** | P2 |
| 中间人 → 公钥替换 | 极低 | 高 | **低** | P3 |
| 恶意扩展 → 数据窃取 | 中 | 高 | **中** | P2 |

---

## 5. 后续改进路线

1. **短期（Phase 1 验收前）：**
   - 部署 CSP 头（`Content-Security-Policy`）
   - 生产环境使用 Tailwind CLI 构建静态 CSS，移除 Play CDN 依赖
   - 编写部署安全清单

2. **中期（Phase 2）：**
   - 将私钥从 `localStorage` 迁移至 `IndexedDB`
   - 探索 `Web Crypto` 不可提取密钥（`extractable: false`）用于私钥（需重新设计密钥备份流程）
   - 添加 Trusted Types 支持

3. **长期（Phase 3+）：**
   - 集成 WebAuthn 用于密钥访问授权
   - 支持硬件安全模块（HSM）或 secure enclave
   - 实现双棘轮（Double Ratchet）协议以支持前向安全性

---

## 6. 参考资料

- [Web Crypto API Specification](https://www.w3.org/TR/WebCryptoAPI/)
- [Content Security Policy Level 3](https://www.w3.org/TR/CSP3/)
- [Subresource Integrity](https://www.w3.org/TR/SRI/)
- [OWASP XSS Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/XSS_Prevention_Cheat_Sheet.html)
