# WATI WhatsApp Automation Agent

一个轻量级 CLI Agent，用自然语言生成并执行 WATI WhatsApp 自动化工作流。

本项目的重点不是覆盖所有 WATI API，而是展示一个可验证、可扩展、默认安全的 Agent 架构：LLM 负责理解意图和生成结构化计划，本地代码负责校验、预览和执行 API 调用。

## Problem Framing

我把这个 assignment 理解为一个内部 automation tool 的 V1：非技术用户用自然语言描述 WhatsApp 运营动作，系统负责把意图翻译成安全、可预览、可执行、可解释的 WATI API workflow。

MVP 目标：

- 支持组合多个 WATI API domain 的工作流。
- 默认预览 API 调用，在任何有副作用的操作前先 dry-run。
- 使用 realistic mock client 保证 demo 稳定，同时保留可替换真实 WATI client 的接口。
- LLM 只负责 plan generation，校验和执行由确定性的本地代码控制。

## Quick Demo

Demo 录屏：

https://drive.google.com/file/d/18NgFbbG8i1egX7ABcWsaR8FBWQjt_6Mt/view?usp=sharing

```bash
python3 -m wati_agent "Find all contacts tagged VIP and send them the renewal_reminder template with their name filled in"
python3 -m wati_agent --execute "escalate 6281234567890"
python3 -m wati_agent "Send a broadcast with the flash_sale template to all contacts who have city = Jakarta"
```

默认模式是 dry-run。有副作用的操作必须显式传入 `--execute`。

dry-run 预览示例：

```text
[Dry Run] Will execute 2 steps:
1. GET /api/v1/getContacts?tag=VIP
2. POST /api/v2/sendTemplateMessage/{whatsappNumber}
Use --execute to run.
```

## Setup

确定性 demo 路径不需要安装第三方依赖。

```bash
python3 --version
```

可选 Qwen/DashScope 配置：

```bash
export DASHSCOPE_API_KEY="your_dashscope_key"
export QWEN_MODEL="qwen-plus"
```

也可以创建本地 `.env` 文件。`.env` 已被 git 忽略：

```bash
DASHSCOPE_API_KEY=your_dashscope_key
QWEN_MODEL=qwen-plus
```

如果不配置 API key，CLI 会使用 deterministic fallback planner，保证 demo 可复现。

## Run

默认 dry-run：

```bash
python3 -m wati_agent "Find all contacts tagged VIP and send them the renewal_reminder template with their name filled in"
```

通过当前配置的 client 执行：

```bash
python3 -m wati_agent --execute "escalate 6281234567890"
```

强制使用 deterministic fallback planner：

```bash
python3 -m wati_agent --provider fallback "escalate 6281234567890"
```

输出 JSON：

```bash
python3 -m wati_agent --json "send a template"
```

## Demo Scenarios

项目支持三个端到端场景：

- `VIP renewal`: 查找带有 `VIP` tag 的联系人，然后给每个人发送 `renewal_reminder` template。
- `Escalation`: 当用户输入 `escalate 6281234567890`，把会话分配给 `Support`，并添加 `escalated` tag。
- `City campaign`: 给 `city=Jakarta` segment 发送 `flash_sale` broadcast。

这些场景覆盖 Contacts、Tags、Messages、Broadcasts、Tickets。

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

### Planner

Planner 使用 Qwen 或 deterministic fallback parser，把自然语言转换为结构化 JSON plan。LLM 不直接调用 API。

示例 plan：

```json
{
  "summary": "Escalate contact to Support and tag as escalated",
  "requires_confirmation": true,
  "steps": [
    {
      "tool": "tickets.assign_team",
      "args": {
        "whatsappNumber": "6281234567890",
        "teamName": "Support"
      }
    },
    {
      "tool": "tags.add",
      "args": {
        "whatsappNumber": "6281234567890",
        "tag": "escalated"
      }
    }
  ]
}
```

### Tool Registry

Tool Registry 定义 Agent 允许使用的动作，并把这些动作映射到 WATI endpoint。

初始工具：

- `contacts.search_by_tag`
- `contacts.search_by_attribute`
- `tags.add`
- `messages.send_template`
- `messages.send_template_batch`
- `broadcasts.send_to_segment`
- `tickets.assign_team`

### Executor

Executor 负责：

- 校验 tool name、必填参数和 step reference。
- 默认预览 API 调用。
- 只有传入 `--execute` 时才执行。
- 报告 request details、outputs、errors 和 partial batch failures。

### WATI Client

Client 层有两个实现：

- `MockWatiClient`: 默认 demo client，包含确定性的 contacts、templates、teams。
- `HttpWatiClient`: 真实 HTTP adapter，方法签名与 mock client 一致，在有有效 WATI credentials 时可以替换。

提交 demo 使用 `MockWatiClient`，这是 assignment 明确允许的 realistic mock。它仍然输出 WATI 形态的 method、endpoint 和 body，让 API 编排过程清晰可见。

### Execution Flow

1. CLI 接收自然语言 instruction。
2. Planner 使用 Qwen 或 fallback parsing 生成结构化 plan。
3. Plan Validator 校验 tool name、必填参数和 step reference。
4. Executor 默认打印 dry-run preview。
5. 当用户传入 `--execute`，Executor 调用配置好的 WATI client。
6. CLI 汇总每一步 request、output 和 error。

## Safety Design

Safety-first agent: LLM 只生成计划；代码负责校验和执行；所有 mutation 必须显式 `--execute`。

- LLM 不能直接调用 WATI API。
- Plan 必须通过本地 tool allowlist 校验。
- 批量发送、broadcast、tagging、ticket assignment 默认只预览。
- 批量执行会分别报告成功和失败的联系人。

## Project Structure

```text
wati_agent/
  __main__.py      CLI entrypoint
  cli.py           argument parsing and output formatting
  planner.py       Qwen planner and fallback planner
  tools.py         allowed tool schema and plan validation
  executor.py      dry-run and execution engine
  client.py        mock WATI client and real HTTP adapter
  mock_data.py     demo contacts, templates, and teams
  models.py        dataclasses shared across modules
tests/
  test_agent.py    planner and executor tests
```

## API Coverage

V1 使用官方 WATI API reference 中的最小可演示 subset：

- `GET /api/v1/getContacts?tag={tag}`
- `GET /api/v1/getContacts`
- `POST /api/v1/addTag/{whatsappNumber}`
- `POST /api/v2/sendTemplateMessage/{whatsappNumber}`
- `POST /api/v1/sendBroadcastToSegment`
- `POST /api/v1/tickets/assign`

## AI/LLM Usage

配置 `DASHSCOPE_API_KEY` 后，系统使用 Qwen 做 intent understanding 和 plan generation。

选择 Qwen 的原因：

- 能较好处理英文和中文的 workflow phrasing。
- 适合输出轻量 orchestration 所需的结构化 JSON plan。
- 架构不绑定具体模型，因为执行逻辑不耦合 LLM provider。

Deterministic fallback planner 用于 demo 稳定性和测试。它不是 LLM 路径的替代，而是避免外部 API 可用性影响 walkthrough。

## Error Handling

系统优先清晰失败，而不是静默猜测。

- 缺少参数时返回具体 clarification request。
- 未知 tool 或缺少必填参数会在执行前 validation failed。
- 批量发送会继续处理独立联系人，并汇总 partial failures。
- API/client errors 会按 step 报告。
- 有副作用的操作必须显式 `--execute`。

## Testability

Planner 和 Executor 不依赖外部服务即可测试。

```bash
python3 -m unittest discover -v
```

测试覆盖：

- VIP renewal planning 和 batch expansion。
- Escalation planning 和 execution。
- City broadcast planning。
- 不完整 instruction 的 clarification flow。

## Build Notes

| Time | Result |
| --- | --- |
| 0-20 min | Project skeleton、CLI entrypoint、README scope |
| 20-50 min | Tool schema、mock data、`MockWatiClient` |
| 50-90 min | Qwen planner、fallback planner、plan validation |
| 90-125 min | Executor、dry-run、execute mode、error handling |
| 125-155 min | 三个 demo scenario 端到端跑通 |
| 155-175 min | Unit tests、README、demo script |
| 175-180 min | Compile、test、demo command verification |

优先级：

- 先保证 end-to-end demo 可靠。
- 先构建 mock client，同时保留 real client interface。
- 先做 dry-run 和 validation，再做 execution。
- 使用 CLI，把时间集中在 agent logic 而不是 UI polish。

Deliberate scope cuts:

- 完整 WATI API 覆盖：V1 只实现 demo 所需 subset。
- Web UI：CLI 更适合 3 小时时间盒，也更聚焦 orchestration。
- 持久化数据库：mock data 足够支撑 end-to-end demo。
- 完整 rollback engine：dry-run 和 per-step reporting 已降低风险，rollback 放到 V2。
- 大规模 queue/rate-limit scheduler：当前 batch handling 已展示设计方向。

## Trade-offs

CLI over Web UI:

Assignment 允许 CLI、chat UI 或 bot。CLI 能把最多时间投入到 agent planning、API orchestration、validation 和 error handling。

Mock-first:

Realistic mock 让 demo 确定、易复现、易评审。真实 HTTP client 使用相同接口，后续只需有效 endpoint/token 即可启用。

LLM plans, code executes:

LLM 适合理解意图，但 validation 和 side effects 必须由确定性代码控制。这更安全，也更容易测试。

## Compliance with WATI Assignment Requirements

- 使用 LLM 做 intent understanding 和 plan generation。
- 跨 Contacts、Tags、Messages、Broadcasts、Tickets 的 multi-domain API orchestration。
- 默认 dry-run / preview。
- Realistic mock client，并保留可替换真实 API 的 adapter。
- Plan validation 和明确 error handling。
- 符合 3 小时时间盒的 scope。
- README 包含 run steps、architecture、AI usage、build notes、trade-offs 和 V2 roadmap。

## V2 Roadmap

- 在 demo path 中使用真实 WATI sandbox credentials。
- 从 `getMessageTemplates` 读取 template metadata。
- 增加 session 内 conversational memory。
- 为高风险动作增加 interactive confirmation prompts。
- 增加 persistent audit logs。
- 增加 retry、pagination 和 rate-limit handling。
- 提供轻量 chat web UI。
