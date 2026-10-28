# xhs-group-chat-insights

一个只读的 Agent Skill 与 Python 工具集：在用户授权范围内抓取或增量更新小红书群聊，离线提取群话题，并分析多个群平时讨论什么、成员关心什么、互动如何发生，以及哪些问题仍未闭环。

目前在 Codex 本地环境中完成了端到端验证；`SKILL.md` 采用 Agent Skills 目录结构，其他支持该规范且具备本地 Shell 能力的 Agent 也可以适配。分析脚本还可以脱离 Agent 单独运行。

> 重要：群聊记录和 Cookie 都属于敏感数据。这个仓库只放 Skill 本身，绝不能提交真实 Cookie、原始群聊、成员名单或含私聊证据的分析报告。

## 能做什么

- 全量抓取、断点续抓和增量更新用户有权访问的群聊
- 从已有 `messages.json` 中离线提取群话题和直接回复状态
- 规范化一个或多个群的聊天记录
- 生成话题、参与者和待深读事件三类研究表
- 基于原文上下文形成“讨论主题—成员关切—群差异—未闭环问题”的分析

不会自动发言、点赞、关注、邀请成员或修改群设置。

## 仓库结构

```text
skills/xhs-group-chat-insights/
├── SKILL.md
├── agents/openai.yaml
├── references/
└── scripts/
```

## 兼容范围

- **Codex 本地环境：** 推荐且已验证。支持自动发现 Skill、`$xhs-group-chat-insights` 调用、二维码登录、抓取和分析完整流程。
- **其他 Agent Skills 客户端：** `SKILL.md`、引用文件和脚本可以复用，但安装位置、自动发现方式和工具权限由宿主决定，尚未逐一验证。
- **ChatGPT / OpenAI API：** Skill 文件可作为工作流资源集成；离线分析最容易迁移。实时抓取依赖本地浏览器、Shell、二维码登录和持久化目录，需要部署方提供这些运行条件。
- **不使用 Agent：** 可以直接运行仓库里的 Python 脚本完成环境检查、登录、抓取、话题提取和分析。

`agents/openai.yaml` 提供 OpenAI 产品中的展示信息，不影响核心 Python 脚本在其他环境运行。

## 在 Codex 中安装（已验证）

### 方式一：安装脚本

通过本仓库安装：

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo q741242673/xhs-group-chat-insights \
  --path skills/xhs-group-chat-insights
```

安装后新开一个 Codex 对话，再使用 `$xhs-group-chat-insights`。

### 方式二：手动复制

```bash
git clone https://github.com/q741242673/xhs-group-chat-insights.git
mkdir -p ~/.codex/skills
cp -R xhs-group-chat-insights/skills/xhs-group-chat-insights ~/.codex/skills/
```

如果目标目录已存在，先人工确认是否需要升级；不要直接覆盖。

## 不使用 Codex：直接运行脚本

克隆仓库后，可以从仓库根目录执行：

```bash
python3 skills/xhs-group-chat-insights/scripts/setup_capture.py --check
python3 skills/xhs-group-chat-insights/scripts/setup_capture.py
python3 skills/xhs-group-chat-insights/scripts/xhs_group_tool.py login-qr
python3 skills/xhs-group-chat-insights/scripts/xhs_group_tool.py doctor
```

抓取、话题提取与分析的参数说明见 `skills/xhs-group-chat-insights/references/`。

## 使用方式

### 群聊链接从哪里获取

最稳定的方式是在电脑浏览器中获取：

1. 登录小红书网页版并进入“消息”。
2. 打开你有权访问的目标群聊。
3. 复制浏览器地址栏中的完整网址，格式通常为：

   ```text
   https://www.xiaohongshu.com/chat/123456789012345678
   ```

工具也接受网址末尾的纯数字群 ID。群聊链接可能包含群标识，请只在自己的本地任务中使用，不要发到公开 Issue 或提交进仓库。

### 已有聊天记录：直接分析

不需要登录，也不需要安装抓取环境。把 ZIP 或完整的 `messages.json` 提供给 Codex，然后说：

```text
请使用 $xhs-group-chat-insights 分析这些群聊记录，告诉我各群平时讨论什么、成员真正关心什么，并标注数据范围与局限。
```

### 没有聊天记录：先连接小红书

首次使用需要完成环境检查和二维码登录。Skill 已内置所需的只读群聊扩展和配置脚本；本机需要 Python 3.10+、Node.js 20+ 和 Git。

```bash
python3 ~/.codex/skills/xhs-group-chat-insights/scripts/setup_capture.py --check
python3 ~/.codex/skills/xhs-group-chat-insights/scripts/setup_capture.py
python3 ~/.codex/skills/xhs-group-chat-insights/scripts/xhs_group_tool.py login-qr
python3 ~/.codex/skills/xhs-group-chat-insights/scripts/xhs_group_tool.py doctor
```

第二条命令会下载依赖；第三条命令显示本地二维码，使用者需用自己的小红书 App 扫码确认。Cookie 只写入权限为 `600` 的本地配置文件，不会打印，也不得上传到 GitHub。

群聊链接**不需要填进上面的安装或登录命令**。`doctor` 检查通过后，回到 Codex 对话，把刚才复制的链接放进请求里：

```text
请使用 $xhs-group-chat-insights 抓取并分析这个群聊：
https://www.xiaohongshu.com/chat/123456789012345678
```

如果不使用 Codex，也可以在命令行直接输入：

```bash
python3 ~/.codex/skills/xhs-group-chat-insights/scripts/xhs_group_tool.py \
  group-chat \
  --chat-url 'https://www.xiaohongshu.com/chat/123456789012345678' \
  --output-dir './chat-exports'
```

更完整的运行说明见 Skill 内的 `references/setup.md` 和 `references/capture.md`。

## 技术说明与使用边界

实时抓取底层使用外部项目 [Spider_XHS](https://github.com/cv-cat/Spider_XHS)。本仓库不包含该项目代码；初始化脚本会下载经过测试的固定版本。它有独立的使用声明和限制，请同时遵守其最新说明、小红书平台规则以及适用法律。

本仓库中的 Skill、分析脚本和只读群聊扩展采用 MIT License。
