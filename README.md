# 小红书工作台 · XHS Workbench

[![Docker build and tests](https://github.com/FUKANFENG/xhs-workbench/actions/workflows/docker-test.yml/badge.svg)](https://github.com/FUKANFENG/xhs-workbench/actions/workflows/docker-test.yml)

一个在本机运行的中文 Web 工作台，把小红书的**群记录、笔记、评论与回复、作者作品、媒体文件和内容发布**放在同一个界面中管理。

扫码登录，数据保存在自己的电脑或 Docker 数据卷中。采集和发布分别授权；图文、视频发布需要在页面确认。项目面向非商业学习与研究，使用条件见下方许可说明。

## 功能

| 模块 | 支持内容 |
|---|---|
| 登录 | 手机扫码、凭据本地保存、登录阶段和失败提示 |
| 群记录 | 首次采集、断点续抓、增量更新、定时更新、关键词搜索、CSV/JSON 导出 |
| 群分析 | 关键词话题统计、参与情况、讨论线索，无需付费大模型 |
| 笔记 | 关键词搜索、综合/最新/热门排序、图文/视频筛选、分享链接采集、正文阅读 |
| 作者作品 | 按作者主页链接或作者 ID 获取作品摘要 |
| 评论与回复 | 一级评论、二级回复、父评论上下文、去重合并、层级筛选、导出 |
| 媒体 | 图片和视频下载、本地预览、单文件保存 |
| 笔记监测 | 最短 5 分钟，最多同时配置 10 篇，串行执行，失败自动暂停 |
| 任务管理 | 进度、取消、超时保护、脱敏错误提示 |
| 内容发布 | 图文/视频草稿、素材上传、标题/正文/话题、提交确认、作品记录核对、发布历史 |
| 发布保护 | 独立创作者会话、实时登录检查、提交后不自动重发、超时/取消/重启后核对结果 |

## 内容发布（1.3）

打开工作台的“内容发布”页（`/publish`）。本地开发版通常为 [http://127.0.0.1:8777/publish](http://127.0.0.1:8777/publish)，Docker 默认是 [http://127.0.0.1:8779/publish](http://127.0.0.1:8779/publish)。

1. 点击“扫码登录”，使用小红书 App 确认创作者中心登录。页面显示账号名称或账号 ID 尾号，发布前请核对。采集凭据与发布会话分别保存，不能假定两者通用。
2. 填写标题、正文、话题并选择图片或视频。素材会保存在当前草稿；保存草稿不会发布。
3. 点击“确认发布”，在确认框检查内容和账号，再点击“确认并提交”。本版支持即时发布。
4. 执行器先读取作品列表，再上传素材、填写内容、点击一次发布，随后核对新作品记录。

图文最多 9 张 JPEG/PNG/WebP，每张 20 MiB；视频仅 1 个 MP4/MOV，最多 500 MiB。标题 1–20 字，正文与话题合计最多 1000 字，话题最多 10 个。文件类型和数量由服务端复核。未匹配的话题会中止提交，可修改后重试。

“已核实提交”表示创作者中心出现相应作品记录，**不表示审核通过或已对公众可见**。遇到网络超时、取消、重启或无法确认的返回时，已经进入提交阶段的草稿会标记为“待核对”，不能再次提交；使用“核对发布结果”只查询记录。查不到时先到官方创作者中心检查，不要直接新建相同内容重发。

发布与采集共用串行任务管理，执行过程中可以停止任务。已经提交的请求无法通过本地取消撤回。当前版本不包含平台定时发布、自动评论、私信或批量重发。

原生 Windows 安装在完成现有本地安装后增加：

```powershell
.\Install-Publish.ps1
# 重启工作台后生效
.\Stop-WebUI.ps1
.\Start-WebUI.ps1
```

脚本下载固定版本 social-auto-upload、安装独立 Python 环境及 Chromium，不会覆盖采集凭据。Docker 构建已包含这些依赖。

## Docker 安装（推荐）

需要 Git、Docker Engine + Compose v2，或已启动的 Docker Desktop（Linux containers）。宿主机不需要安装 Python、Node.js 或浏览器。

```console
git clone https://github.com/FUKANFENG/xhs-workbench.git
cd xhs-workbench
docker compose up -d --build --wait
```

打开 **[http://127.0.0.1:8779](http://127.0.0.1:8779)**，点击“生成登录二维码”，用手机小红书扫码并确认。

Windows 也可以运行一键安装脚本：

```powershell
.\Install-Docker.ps1
# 更换端口：
.\Install-Docker.ps1 -Port 8780
```

首次构建会从 GitHub 下载固定版本的采集、发布源码，并安装 Python/Node 依赖和发布使用的 Chromium，需要能访问 GitHub、容器镜像源、PyPI、npm 和浏览器下载源。后续构建会复用缓存。发布任务按需启动后台浏览器。

如果构建期间下载依赖失败，或 GitHub 返回 TLS 连接中断，请先检查 Docker 的网络连接，再重试启动命令；已完成的构建层会复用。不需要关闭 TLS 证书校验。

默认只将端口绑定到本机 `127.0.0.1`。容器使用非 root 用户，程序目录只读，数据写入独立命名卷。此配置适合单人本地使用，不提供面向公网的多用户登录系统。

### 常用命令

```console
docker compose ps
docker compose logs --tail=100 web
docker compose stop
docker compose start
docker compose down
```

`stop` 和 `down` 不删除命名数据卷。**需要保留数据时，不要使用 `docker compose down -v`。**

升级：

```console
git pull
docker compose up -d --build --wait
```

长期修改端口：复制 `.env.example` 为 `.env`，将 `XHS_PORT` 改为需要的端口，再启动。Windows 脚本的 `-Port` 参数仅作用于本次命令。

### 数据与备份

Compose 的 `xhs-data` 命名卷挂载到容器 `/data`，实际卷名默认是 `xhs-workbench_xhs-data`。

| 目录/文件 | 内容 |
|---|---|
| `/data/auth/` | 登录凭据、临时二维码 |
| `/data/auth/publisher/` | 独立创作者浏览器会话、临时发布二维码、最近登录检查；不要公开分享 |
| `/data/exports/` | 群记录、采集进度 |
| `/data/derived/`、`/data/analysis/` | 群记录整理和分析结果 |
| `/data/notes/notes.sqlite3` | 笔记、评论、监测配置和内部访问参数 |
| `/data/notes/media/` | 已下载图片和视频 |
| `/data/preferences.json` | 群备注、调度参数、最近登录结果 |
| `/data/publishing/publishing.sqlite3` | 草稿、版本、发布阶段、结果核对依据；不确定提交会持久保留 |
| `/data/publishing/<草稿编号>/` | 用户上传的待发布素材 |

备份时先停止服务，避免复制不一致的 SQLite 文件：

```console
docker compose stop
docker compose cp web:/data ./xhs-backup
docker compose start
```

备份包含凭据和原始内容，应留在自己的存储中。分享结果请使用页面的 CSV/JSON 导出。Docker 默认使用新的数据卷，不会自动复制 Windows 本地版的账户和数据。

远程服务器建议通过 SSH 转发访问本机端口：

```console
ssh -L 8779:127.0.0.1:8779 your-user@your-server
```

随后在自己的电脑打开 `http://127.0.0.1:8779`。不要直接把此单用户工具暴露到公网。

## 使用流程

1. **扫码登录**：在群记录工作台完成登录，笔记和评论共用这份凭据。
2. **群记录**：输入有权访问的群链接或群 ID，首次采集后可续抓、增量更新和分析。
3. **笔记采集**：搜索关键词保存摘要，或粘贴完整笔记链接、分享短链接。摘要与已采集详情分别标记。
4. **评论与回复**：设置一级评论上限，可选采集二级回复；回复总量单独设限。
5. **作者作品**：输入主页链接/ID，或从已采集笔记的详情进入作者作品采集。
6. **媒体**：打开笔记详情，点击“下载图片 / 视频”；完成后可预览或保存文件。
7. **定时检查**：在单篇笔记的评论页配置，修改间隔或数量后点击“保存设置”。

任务串行执行。暂停监测只停止后续调度，当前任务需在任务区取消。服务重启或重新扫码后，定时任务暂停，设置保留供手动恢复。

## 范围与限制

- 可采集内容由账号权限和平台返回决定，不保证全部历史、删除内容或入群前的记录。
- 定时更新是分页轮询，不是秒级推送。达到上限时保留旧数据并标记本轮范围。
- 评论按 ID 合并，不会因本轮未返回而删除旧记录，不作为平台撤回内容的严格镜像。
- 二级回复限于本轮选取的一级评论下，总量单独设限。
- 每篇最多下载 30 个媒体文件，单张图片最多 64 MiB、单个视频最多 512 MiB。视频预览取决于浏览器支持的编码。
- 群分析基于关键词与互动统计，不包含内置大模型总结、图片 OCR、语音转写或自动通知推送。
- 平台接口可能变化。升级采集引擎前，应重新验证登录与关键接口。

## Windows 原生安装

需要 Python 3.11、Node.js 20+、Git；笔记模块另外需要 `uv`。

```powershell
.\Install-Local.ps1
.\Install-Notes.ps1
.\Start-WebUI.ps1
```

默认打开 `http://127.0.0.1:8777`。停止时运行 `Stop-WebUI.ps1`。新安装的数据默认位于仓库 `data/`；已有工作区的数据路径继续保留，也可通过 `XHS_LOCAL_DATA` 指定。

## 结构与开发

```text
webapp/                         Web API、任务调度、数据库和前端
integration/                    独立 MediaCrawler 适配进程
skills/xhs-group-chat-insights/  群记录采集和离线分析工具
docker/                         容器构建与启动脚本
tests/                          存储、接口、分页、调度和安全边界测试
Dockerfile / compose.yaml       容器安装配置
```

Web 服务/群记录和笔记采集使用两套独立 Python 环境，避免依赖冲突。它们共用数据目录和串行任务管理。

固定采集源码版本：

- Spider_XHS：`23dab803a0465e35e01cf87137353a900b47e93d`
- MediaCrawler：`68d9ef1c1da22eab75940dfd7cec259907006538`

运行容器内测试：

```console
docker build --target test -t xhs-workbench:test .
```

测试使用临时目录和合成数据，不需要真实账号。验证范围见 [验证记录](LOCAL_VERIFICATION.md)。原始 Agent Skill 说明保留在 [上游说明](docs/UPSTREAM_SKILL_README.md)。

2026-10-29 已在 GitHub Actions 的 Linux amd64 环境完成镜像构建、53 项测试和只读容器启动检查。镜像内两套采集环境均就绪，初始数据卷无登录凭据。真实账号采集的既有验证来自 Windows 本地版；Docker 首次使用仍需要自行扫码。

## 来源与许可

基于 [q741242673/xhs-group-chat-insights](https://github.com/q741242673/xhs-group-chat-insights) 扩展，保留其 MIT 版权声明和 [LICENSE](LICENSE)。

群记录和登录使用 [cv-cat/Spider_XHS](https://github.com/cv-cat/Spider_XHS)，笔记、评论和媒体使用 [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)。MediaCrawler 采用 **非商业学习使用许可证 1.1**，商业用途需要版权所有者书面同意。Spider_XHS 的 README 也声明仅限学习交流、禁止商业化。

依赖的许可与上游声明不会被本仓库的 MIT 许可替代。完整说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。Docker 构建保留第三方源码中的版权和许可文件。
