# 小红书工作台 · XHS Workbench

[![Docker build and tests](https://github.com/FUKANFENG/xhs-workbench/actions/workflows/docker-test.yml/badge.svg)](https://github.com/FUKANFENG/xhs-workbench/actions/workflows/docker-test.yml)

一个在本机运行的中文 Web 工作台，把小红书的**群记录、笔记、评论与回复、作者作品和媒体文件**放在同一个界面中管理。

扫码登录，数据保存在自己的电脑或 Docker 数据卷中。不自动发言、点赞、关注或发送私信。项目面向非商业学习与研究，使用条件见下方许可说明。

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

首次构建会从 GitHub 下载固定版本的采集源码，并安装 Python/Node 依赖，需要能访问 GitHub、容器镜像源、PyPI 和 npm。后续构建会复用缓存。容器不会下载或启动浏览器。

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
| `/data/exports/` | 群记录、采集进度 |
| `/data/derived/`、`/data/analysis/` | 群记录整理和分析结果 |
| `/data/notes/notes.sqlite3` | 笔记、评论、监测配置和内部访问参数 |
| `/data/notes/media/` | 已下载图片和视频 |
| `/data/preferences.json` | 群备注、调度参数、最近登录结果 |

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
