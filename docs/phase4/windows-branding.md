# Windows 品牌与版本验收（T16，P1）

> 更新日期：2026-10-07；核对代码基线：`f2f60ec`。现行说明已按代码和验收证据更新。
> 当前状态见 [项目现状](../current-status.md)；文档用途与归档规则见 [文档维护索引](../documentation-status.md)。

## 本次更新

T16/#143 已闭环，包含 PR #317/#318 的名称/图标/版本、关于面板、EXE 标识清理及资源校验。运行时 AUMID 与 build.appId 为 pro.ichat.desktop（PR #327）；版本 1.0.0。未配置商业代码签名证书；资源编辑不等于数字签名。

T16 统一安装器、应用窗口和快捷方式的产品名称、图标与版本，并提供可访问的关于信息。系统托盘在 T17 创建后复用这些品牌资源并实测；代码数字签名另需签名证书，不以资源编辑或 CI runner 权限替代。

## 产品资源

- 产品名称：iChat Pro；版本来源：desktop/package.json。
- build/icon.ico：16、24、32、48、64、128、256px 七种尺寸。
- build/icon.png：从同一 ICO 的 48px PNG 帧提取，供原生关于弹窗使用。替换图标时应同步更新两个文件，构建验证会检查一致性。
- extraResources 将两份图标装入 resources/branding；窗口、关于弹窗均从该目录加载。开发启动从 desktop/build 加载。
- 关于入口：帮助 → 关于 iChat Pro；快捷键 Ctrl+Alt+A。名称、版本、版权与图标由 Electron 原生关于面板展示。
- app.name 设为展示名时，保留原有 userData、sessionData 路径，继续使用已有 ichat-pro-desktop 数据目录，避免升级后切换到新 profile。

## Windows 构建验证

在 desktop 目录执行：

```powershell
npm ci --no-audit --no-fund
npm run test:branding
npm run test:installer
npm run dist
```

test:branding 实际构建 Windows unpacked 产物，调用生产 afterPack 钩子，再检查：

| 项目 | 要求 |
| --- | --- |
| ProductName | iChat Pro |
| FileDescription | iChat Pro desktop client |
| CompanyName | iChat Pro Team |
| InternalName | iChat Pro |
| OriginalFilename | iChat Pro.exe |
| FileVersion / ProductVersion | 与 package.json 的版本一致 |
| exe 图标 | PE 图标组中七种尺寸与源 ICO 的图像内容逐字节一致 |
| 关于 PNG | 与源 ICO 的 48px PNG 帧一致 |
| 运行时文件 | app-branding.js 在 app.asar 中，两个图标按配置复制到 resources/branding |
| profile 名称 | 打包后的 package.name 仍为 ichat-pro-desktop |

篡改源图标的对照必须被校验器拒绝。构建挂起、资源丢失或原生属性检查失败均返回失败；唯一临时构建目录在校验后清理。Windows CI 的 windows-installer job 同时运行该验证和既有 24 项 NSIS 隔离断言，不安装或卸载真实应用。

仓库根目录的 npm run test:e2ee 另外验证展示名变更仍保留 userData/sessionData 路径，关于信息使用运行时版本，菜单入口会调用原生关于面板。

## 2026-10-05 本机复核

- PR #317 产物的 ProductName/版本/图标已生效，但 InternalName、OriginalFilename 仍为 electron.exe；Help 菜单为空，无关于入口。任务表也尚未回填 T16 状态。
- 修复后打包应用自动加载云端登录页；原生关于弹窗显示 iChat Pro、1.0.0、版权和品牌图标，标题亦为 iChat Pro。
- 修复后的客户端 exe 与 NSIS Setup 的七种图标帧均与源 ICO 一致；Setup 的 ProductName 和两个版本字段正确。
- npm run test:branding、npm run test:installer（24/24）、npm run test:e2ee、git diff --check 通过。
- 本轮只直接运行 unpacked 客户端核验品牌，未重复此前已通过的安装/卸载生命周期；T15 的正式记录保持原验收范围。
