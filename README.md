# AutoDL Fresh

AutoDL 普通容器实例的中文桌面保活助手，使用 Python、Playwright 和 PySide6 构建。

**当前正式版：0.5.0** · Windows 10 / 11 x64 · **仅限非商业使用**

[下载 Windows 正式版](https://github.com/wanfeng562-code/autodl-fresh/releases/download/v0.5.0/AutoDLFresh-0.5.0-windows-x64.zip)
| [发布说明](https://github.com/wanfeng562-code/autodl-fresh/releases/tag/v0.5.0)
| [许可协议](LICENSE)
| [更新日志](CHANGELOG.md)

## 功能

- 首次手动登录后保存 AutoDL 网站登录态；浏览器专用配置与日常浏览器隔离，禁用浏览器账号、同步和用户扩展。
- 只读检查遍历全部网页分页，并核对实例总数。
- **检查并刷新**：默认处理已关机、按量计费、距离释放不足 3 天的实例。
- **全部刷新**：忽略剩余天数，处理全部符合条件的已关机按量实例，不受主页分页或搜索影响。
- 逐个完成无卡开机、关机，并核验释放倒计时；失败时停止后续队列，保留恢复记录。
- 实例列表默认每页 10 条，可选 20 / 50 / 100 条，支持搜索、翻页和输入页码。
- 每日计划、系统托盘、可选 Windows 登录后自启动。
- 前台 / 后台模式、实例自动滚动定位、登录保存提示和空白窗口清理。
- 可查看和更换数据目录；设置中的下拉框和数值控件需先点击才能用滚轮修改。
- 逐显示器 DPI 支持，适配 Windows 系统缩放。

![AutoDL Fresh 实例总览（模拟数据）](docs/overview.png)

## 下载使用

1. 下载上方 Windows 压缩包，解压整个 `AutoDLFresh` 文件夹。
2. 双击 `AutoDLFresh.exe`。**不要单独移动 exe**，`_internal` 和许可文件需与它一起保留。
3. 点击「登录 AutoDL」，在专用窗口中手动登录网站，再返回应用点击「保存登录态」。无需登录 Microsoft / Google 浏览器账号。
4. 先用「只读检查」核对实例。使用「检查并刷新」按阈值处理，或点「全部刷新」统一刷新。
5. 在「偏好设置」按需启用每日计划、自启动和后台模式，并保存设置。

默认使用已安装的 Microsoft Edge，无需安装 Python 或另行下载 Chromium。
首次登录始终显示浏览器；账号密码、扫码登录与人机验证由用户手动完成。
应用不绕过验证码，平台是否允许登录由 AutoDL 决定。

无卡开机会产生费用，以 AutoDL 的配置费、最低消费和实际计费规则为准。
每日任务需要本机开机、联网且应用运行；睡眠或退出时无法执行。恢复运行后可补检，
每个北京时间自然日最多自动尝试一次。手动全部刷新不改变每日任务的天数阈值。

升级前先从托盘退出旧程序，再启动新版。已有登录态和设置继续使用；
如启用了 Windows 自启动，在新版点一次「保存设置」更新启动路径。

## 执行与异常恢复

两种刷新都重新读取全部网页分页，并在每次开机前重新检查实例状态。
原本运行、非按量计费、倒计时不可识别或已到释放时间的实例不会被启动；
已有无卡实例运行或启动中时暂停刷新。仅支持普通容器实例，不操作 Pro 或弹性部署。
请在 AutoDL 表格列设置中保留实例 ID、状态、付费方式、释放时间和操作列。

开机必须获得平台成功响应，随后确认「运行中 + 无卡模式」，再关机并核验
「已关机 + 释放倒计时接近完整保留期」。通常还要求倒计时大于刷新前数值。
全部刷新遇到原本就接近完整保留期的实例时，允许分钟精度导致前后倒计时相同，
但仍须有平台已接受开机、关机和完整倒计时的证据。开机响应不确定时不会放宽核验或重复开机。

「停止后续操作」会停止剩余队列；已被平台接受的开机操作仍优先完成关机与核验。
异常恢复失败时保留实例快照和待核验记录。请在网页人工核对待处理实例；
已经确认关机且无进行中的开机操作时，可使用「解除暂停」重新读取状态并记录人工处理。

网络中断、断电或强制结束程序可能导致无法及时关机。重要数据请另行备份。
本软件不是 AutoDL 官方产品，与 AutoDL 不存在隶属或授权背书关系。

## 本地数据

默认位于 `%LOCALAPPDATA%\AutoDLFresh`。在「偏好设置 → 数据目录」查看或打开。
可选择独立空文件夹，点「保存并重启」复制设置、登录态和记录；原目录保留，
浏览器缓存重新生成。复制失败时仍使用旧目录，新旧目录不能互相包含。

- `auth.json`：AutoDL 登录态，**不要分享或提交到仓库**。
- `settings.json`、`scheduler.json`：设置与每日计划状态。
- `pending.json`：尚未完成操作的恢复记录。
- `history.json`、`activity.log`：执行摘要、实例快照和日志。
- `browser-autodl-only-v1-*`：隔离浏览器配置及网站缓存，不要分享。

默认目录下的 `data-location.json` 记录所选路径，进程锁也固定保留在默认目录。
高级用户可用 `AUTODL_FRESH_DATA_DIR` 环境变量指定目录；该变量优先于界面设置。
应用仅保存 AutoDL 网站登录态，不读取日常浏览器配置或记录账号密码。

## 从源码运行

需要 Python 3.11+ 和已安装的 Edge / Chrome：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe run_app.py
```

也可双击 `setup.bat` 安装依赖，再用 `start.bat` 启动。
如选择 Playwright Chromium，先安装它并使用「Playwright 兼容模式」：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

## 验证和构建

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check autodl_fresh tests tools run_app.py
```

测试只访问本机模拟页面、临时专用浏览器和离屏 GUI，不使用用户登录态，
不访问真实 AutoDL 实例，也不产生云费用。普通窗口测试需要允许 Edge 渲染进程的桌面环境。

正式版对应的 Qt / PySide6 源码和许可收集：

```powershell
.\.venv\Scripts\python.exe tools/collect_third_party.py
.\build.ps1
```

发布构建使用 Python 3.12.4、PySide6 / Shiboken 6.10.2、Playwright 1.64.1、
PyInstaller 6.19.0。构建输出 `dist\AutoDLFresh` 和 `releases` 下的 Windows 压缩包、
第三方源码包及 SHA-256 校验文件。第三方源码包不含本项目的非商业许可限制；
Qt / PySide6 的许可、源码位置和替换 / 重新链接说明见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 许可与版权

**Copyright (c) 2026 wanfeng562-code. All rights reserved.**

本项目原创代码、界面、图标及文档采用 [AutoDL Fresh 非商业使用许可 1.0](LICENSE)：
允许他人下载、非商业使用、研究、修改和免费分享；未经版权人单独书面授权，
禁止销售、收费服务、商业代运营、商业产品集成及其他商业用途。
**版权人本人保留商业使用、销售及另行商业授权的权利。**

这是源码可见的非商业授权项目，不是 MIT / Apache / GPL 授权的开源项目。
第三方库的版权和许可独立保留，非商业限制不覆盖它们。
商业授权请通过 [GitHub Issues](https://github.com/wanfeng562-code/autodl-fresh/issues) 联系版权人。
