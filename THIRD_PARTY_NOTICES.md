# 第三方组件

## social-auto-upload（发布适配器）

来源：https://github.com/dreammis/social-auto-upload

固定版本：3847fff01f5434a8a32829833163cd6723cabc59。MIT License，Copyright (c) 2023 dreammis。完整上游源码和 LICENSE 保留在 `runtime/SocialAutoUpload`（Docker 中为 `/opt/runtimes/SocialAutoUpload`）。本项目调用其小红书登录和内容填写方法；本地适配器独立处理超时、一次提交及结果核对。

发布使用 Patchright 1.58.2（Apache-2.0）及其 Chromium 浏览器。Python 依赖固定在 `requirements-publish.lock`，运行环境保留第三方包的许可证；Chromium 保留发行包内的第三方许可文件。新增发布组件的许可不会改变下述采集组件的使用限制。

## xhs-group-chat-insights

来源：https://github.com/q741242673/xhs-group-chat-insights

本项目基于其群记录工具与 Agent Skill 扩展，保留根目录及技能目录中的 MIT LICENSE 和原有版权声明。

## Spider_XHS

来源：https://github.com/cv-cat/Spider_XHS

固定版本：23dab803a0465e35e01cf87137353a900b47e93d。

用于扫码登录、签名和群历史接口。该固定版本的 README 声明“仅供学习交流使用，禁止任何商业化行为”。应遵守上游声明；本项目的 MIT 许可不替代依赖的使用条件。Docker 构建保留上游源码及 README。

## MediaCrawler

来源：https://github.com/NanmiCoder/MediaCrawler

固定版本：68d9ef1c1da22eab75940dfd7cec259907006538

版权所有 (c) 2024 relakkes@gmail.com。

使用 NON-COMMERCIAL LEARNING LICENSE 1.1（非商业学习使用许可证 1.1）。本地适配器调用其小红书客户端及请求签名模块，HTTP 请求头遵循该版本客户端配置。完整源码与 LICENSE 保留在本地数据目录 runtime/MediaCrawler。

仅限非商业学习、研究使用；使用、副本及合并代码应保留版权与许可声明；商业用途需要版权所有者书面同意。原项目的 MIT 许可不会替代 MediaCrawler 的许可要求。

许可证原文：https://github.com/NanmiCoder/MediaCrawler/blob/68d9ef1c1da22eab75940dfd7cec259907006538/LICENSE

MediaCrawler 使用的 xhshow 及其他依赖保留各自许可证，版本记录在其 uv.lock。
