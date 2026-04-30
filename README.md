# WATI WhatsApp Automation Agent

A lightweight CLI agent that turns natural-language WhatsApp business workflow requests into previewable and executable WATI API plans.

The project intentionally focuses on agent architecture and orchestration rather than broad endpoint coverage. The LLM understands intent and generates a structured plan; local code validates, previews, and executes the API calls.

## Problem Framing

I interpreted the assignment as a V1 internal automation tool for non-technical operators: a user describes what they want to happen in natural language, and the agent translates that intent into a safe, explainable WATI API workflow.

MVP goals:

- Support workflows that combine multiple WATI API domains.
- Preview API calls by default before any side-effecting operation.
- Use a realistic mock client for deterministic demos, while keeping a swappable real WATI client interface.
- Keep the LLM limited to plan generation; validation and execution stay in deterministic application code.

## Quick Demo

```bash
python3 -m wati_agent "Find all contacts tagged VIP and send them the renewal_reminder template with their name filled in"
python3 -m wati_agent --execute "escalate 6281234567890"
python3 -m wati_agent "Send a broadcast with the flash_sale template to all contacts who have city = Jakarta"
```

Default mode is dry-run. Mutating actions require explicit `--execute`.

Example dry-run preview:

```text
[Dry Run] Will execute 2 steps:
1. GET /api/v1/getContacts?tag=VIP
2. POST /api/v2/sendTemplateMessage/{whatsappNumber}
Use --execute to run.
```

## Setup

No third-party dependency is required for the deterministic demo path.

```bash
python3 --version
```

Optional Qwen/DashScope configuration:

```bash
export DASHSCOPE_API_KEY="your_dashscope_key"
export QWEN_MODEL="qwen-plus"
```

You can also create a local `.env` file. It is ignored by git:

```bash
DASHSCOPE_API_KEY=your_dashscope_key
QWEN_MODEL=qwen-plus
```

Without an API key, the CLI uses a deterministic fallback planner so the demo remains reproducible.

## Run

Dry-run by default:

```bash
python3 -m wati_agent "Find all contacts tagged VIP and send them the renewal_reminder template with their name filled in"
```

Execute through the configured client:

```bash
python3 -m wati_agent --execute "escalate 6281234567890"
```

Force deterministic fallback planning:

```bash
python3 -m wati_agent --provider fallback "escalate 6281234567890"
```

JSON output:

```bash
python3 -m wati_agent --json "send a template"
```

## Demo Scenarios

The demo supports three end-to-end scenarios:

- `VIP renewal`: find contacts tagged `VIP`, then send each one the `renewal_reminder` template.
- `Escalation`: when the user says `escalate 6281234567890`, assign the conversation to `Support` and add the `escalated` tag.
- `City campaign`: send the `flash_sale` broadcast to the `city=Jakarta` segment.

These scenarios cover Contacts, Tags, Messages, Broadcasts, and Tickets.

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

The planner uses Qwen or a deterministic fallback parser to convert natural language into a structured JSON plan. The LLM never calls APIs directly.

Example plan:

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

The tool registry defines the only actions the agent is allowed to use and maps them to WATI endpoints.

Initial tools:

- `contacts.search_by_tag`
- `contacts.search_by_attribute`
- `tags.add`
- `messages.send_template`
- `messages.send_template_batch`
- `broadcasts.send_to_segment`
- `tickets.assign_team`

### Executor

The executor:

- Validates tool names, required arguments, and step references.
- Previews API calls by default.
- Executes only when `--execute` is provided.
- Reports request details, outputs, errors, and partial batch failures.

### WATI Client

The client layer has two implementations:

- `MockWatiClient`: default demo client with deterministic contacts, templates, and teams.
- `HttpWatiClient`: real HTTP adapter with the same method signatures, ready to swap in when valid WATI credentials are available.

The submitted demo uses `MockWatiClient`, which is allowed by the assignment. It still prints WATI-shaped method, endpoint, and body information so the orchestration is visible.

### Execution Flow

1. CLI receives a natural-language instruction.
2. Planner generates a structured plan with Qwen or fallback parsing.
3. Plan Validator checks tool names, required arguments, and step references.
4. Executor prints a dry-run preview by default.
5. With `--execute`, Executor calls the configured WATI client.
6. CLI summarizes every request, output, and error.

## Safety Design

Safety-first agent: LLM plans only; code validates and executes; all mutations require explicit `--execute`.

- The LLM cannot directly call WATI APIs.
- Plans are checked against a local allowlist of tools.
- Bulk sends, broadcasts, tagging, and ticket assignment are previewed by default.
- Batch execution reports successful and failed contacts separately.

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

V1 uses a focused subset of the provided WATI API reference:

- `GET /api/v1/getContacts?tag={tag}`
- `GET /api/v1/getContacts`
- `POST /api/v1/addTag/{whatsappNumber}`
- `POST /api/v2/sendTemplateMessage/{whatsappNumber}`
- `POST /api/v1/sendBroadcastToSegment`
- `POST /api/v1/tickets/assign`

## AI/LLM Usage

Qwen is used for intent understanding and plan generation when `DASHSCOPE_API_KEY` is configured.

Why Qwen:

- It handles both English and Chinese workflow phrasing well.
- It can produce structured JSON plans for lightweight orchestration tasks.
- It keeps the architecture model-agnostic because execution is not coupled to the LLM provider.

The deterministic fallback planner exists for demo reliability and testing. It is not a replacement for the LLM path; it prevents external API availability from blocking the walkthrough.

## Error Handling

The system fails clearly instead of guessing silently.

- Missing parameters return a concrete clarification request.
- Unknown tools or missing required arguments fail validation before execution.
- Batch sends continue across independent contacts and summarize partial failures.
- API/client errors are reported per step.
- Side-effecting operations require explicit `--execute`.

## Testability

The planner and executor are testable without external services.

```bash
python3 -m unittest discover -v
```

The tests cover:

- VIP renewal planning and batch expansion.
- Escalation planning and execution.
- City broadcast planning.
- Clarification flow for incomplete instructions.

## Build Notes

| Time | Result |
| --- | --- |
| 0-20 min | Project skeleton, CLI entrypoint, README scope |
| 20-50 min | Tool schema, mock data, `MockWatiClient` |
| 50-90 min | Qwen planner, fallback planner, plan validation |
| 90-125 min | Executor, dry-run, execute mode, error handling |
| 125-155 min | Three demo scenarios working end-to-end |
| 155-175 min | Unit tests, README, demo script |
| 175-180 min | Compile, test, and demo command verification |

Priorities:

- Make the end-to-end demo reliable.
- Build the mock client first, while preserving the real client interface.
- Implement dry-run and validation before execution.
- Use CLI to keep the focus on agent logic instead of UI polish.

Deliberate scope cuts:

- Full WATI API coverage: V1 implements only the subset needed for the demo.
- Web UI: CLI best fits the 3-hour timebox and keeps attention on orchestration.
- Persistent database: mock data is enough for an end-to-end demo.
- Full rollback engine: dry-run and per-step reporting reduce risk; rollback is a V2 feature.
- Large-scale queue/rate-limit scheduler: current batch handling demonstrates the design direction.

## Trade-offs

CLI over Web UI:

The assignment allows CLI, chat UI, or bot. CLI gives the most time to agent planning, API orchestration, validation, and error handling.

Mock-first:

The realistic mock makes the demo deterministic and easy to review. The real HTTP client follows the same interface and can be enabled later with valid endpoint/token configuration.

LLM plans, code executes:

The LLM is valuable for intent parsing, but deterministic code must own validation and side effects. This is safer and easier to test.

## Compliance with WATI Assignment Requirements

- LLM used for intent understanding and plan generation.
- Multi-domain API orchestration across Contacts, Tags, Messages, Broadcasts, and Tickets.
- Dry-run / preview by default.
- Realistic mock client with swappable real API adapter.
- Plan validation and explicit error handling.
- 3-hour timebox compliant scope.
- Clear README with run steps, architecture, AI usage, build notes, trade-offs, and V2 roadmap.

## V2 Roadmap

- Use real WATI sandbox credentials in the demo path.
- Read template metadata from `getMessageTemplates`.
- Add conversational memory within a session.
- Add interactive confirmation prompts for risky actions.
- Add persistent audit logs.
- Add retry, pagination, and rate-limit handling.
- Provide a lightweight chat web UI.
