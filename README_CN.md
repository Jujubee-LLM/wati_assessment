# WATI WhatsApp Automation Agent

一个轻量级 CLI Agent，用自然语言生成并执行 WATI WhatsApp 自动化工作流。

本项目的重点不是覆盖所有 WATI API，而是展示一个可验证、可扩展、默认安全的 Agent 架构：LLM 负责理解意图和生成计划，程序负责校验、预览和执行 API 调用。

## Problem Framing

我把这个 assignment 理解为一个内部 automation tool 的 V1：非技术用户用自然语言描述 WhatsApp 运营动作，系统负责把意图翻译成可预览、可执行、可解释的 WATI API plan。

MVP 目标：

- 覆盖多个 WATI API domain 的组合工作流，而不是追求完整 endpoint 覆盖。
- 默认 dry-run，先解释计划再执行，降低误发消息和误改客户状态的风险。
- 使用 realistic mock 保证 demo 稳定，同时通过 client interface 保留真实 WATI API 替换点。
- LLM 只参与 plan generation，API execution 由本地代码验证和控制。

## Setup

不需要安装第三方依赖，Python 标准库即可运行。

```bash
python3 --version
```

如果要使用 Qwen planner，配置 DashScope API key：

```bash
export DASHSCOPE_API_KEY="your_dashscope_key"
export QWEN_MODEL="qwen-plus"
```

也可以创建本地 `.env` 文件。`.env` 已被 `.gitignore` 排除，不应提交到 GitHub：

```bash
DASHSCOPE_API_KEY=your_dashscope_key
QWEN_MODEL=qwen-plus
```

如果不配置 key，CLI 会自动使用 deterministic fallback planner，三个 demo 场景仍可稳定运行。

## Run

```bash
python3 -m wati_agent "Find all contacts tagged VIP and send them the renewal_reminder template with their name filled in"
```

默认是 dry-run，只展示将要调用的 API，不执行副作用操作。

```bash
python3 -m wati_agent --execute "escalate 6281234567890"
```

`--execute` 会执行计划。当前版本使用 mock client，输出等价的 HTTP method、endpoint 和 request body。

## Demo Scenarios

项目支持 3 个端到端场景：

- `VIP renewal`: 查询 `VIP` 联系人，并批量发送 `renewal_reminder` template。
- `Escalation`: 输入 `escalate 6281234567890` 后，把会话分配给 `Support` team，并添加 `escalated` tag。
- `City campaign`: 给 `city = Jakarta` 的 segment 发送 `flash_sale` broadcast。

这 3 个场景覆盖 Contacts、Tags、Messages、Broadcasts、Tickets 多个 API domain。

## Architecture

```text
User Instruction
      |
      v
CLI -> Planner -> Plan Validator -> Executor -> WATI Client
        |              |              |            |
        v              v              v            v
      LLM        Tool Registry   Dry-run/Run   Mock/Real API
```

LLM 只负责生成计划，本地代码负责校验和执行。
