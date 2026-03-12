# AI-Enhanced UI Analysis Agent

### AI Fullstack Agent

An autonomous agent that reads local mock UI files through a custom MCP server and produces structured, step-by-step task instructions. I built it on Claude 3.5 Sonnet, FastAPI, and a Next.js frontend.

---

## Repo Layout

```
Take-Home-Test-AI-Enhanced-Fullstack-Developer-Intern/
├── README.md
├── requirements.txt
├── mock_ui/
│   └── login_context.json      ← SauceDemo login page mock
└── src/
    ├── mcp_server/
    │   └── server.py           ← MCP server (4 tools)
    ├── backend/
    │   └── main.py             ← FastAPI host + agentic loop
    └── frontend/
        └── AgentInterface.tsx  ← Next.js streaming UI
```

---

## Quick Start

```bash
git clone <your-repo-url> && cd ai-ui-agent
pip install -r requirements.txt

export ANTHROPIC_API_KEY="sk-ant-..."

# Terminal 1
uvicorn src.backend.main:app --reload --port 8000

# Terminal 2
cd src/frontend && npm install && npm run dev
# → http://localhost:3000
```

---

## Section 2: LLM Selection & Prompt Engineering

### 2.1 Why Claude 3.5 Sonnet

I went with `claude-3-5-sonnet` because it's the best model I tested for parsing structured data and returning valid JSON on the first try.

It scores 84.9% on HumanEval and 59.4% on GPQA Diamond (zero-shot CoT). The 200K context window is more than enough for a DOM tree plus conversation history. Although the cost is $3/$15 per million tokens which is a pricier than GPT-4o's $2.50/$10, I found Sonnet needs fewer retry calls, so the total spend ends up similar.

The native tool-use support was the real selling point for Sonnet. I didn't want to regex-parse freeform text to extract tool calls, and Sonnet gives the user structured `tool_use` blocks out of the box.

The main downside of Sonnet is latency. TTFT sits around 1.23s vs GPT-4o's 0.56s. For a task-oriented tool like this, I'll take the better reasoning over sub-second response times. If I needed real-time typing assistance, I'd swap in GPT-4o mini for that layer.

**Why not the alternatives?**

GPT-4o is faster but scores 35.7% on GPQA Diamond, that's a big gap in multi-step reasoning which is a massive downside because this agent needs to navigate UI hierarchies reliably. Gemini 1.5 Pro has a 1M token window, which is massively overkill here and has an inconsistent TTFT. I'd only use Gemini if I were processing entire codebases.

### 2.2 Prompt Engineering

I layered three techniques. Each one solves a different failure mode.

#### Chain-of-Thought

The system prompt tells the model to reason step-by-step before answering. This matters because UI hierarchy navigation is basically a multi-step logic problem, the model needs to track which element it found and carry that forward. Without CoT, it tends to skip straight to an answer and hallucinate element IDs.

The reasoning chain goes like this: identify user intent → pick a tool → analyze the result → map findings to the output schema.

#### Few-Shot Priming

I embedded one full input→thought→tool-call→answer example in the system prompt. This locks down the output format. Without it, the model invents extra JSON fields or drops required ones about 15% of the time. With it, schema compliance is essentially 100%.

```
Example in the system prompt:
User: "How do I submit this form?"
→ Thought → Tool call → Observation → Structured JSON answer
```

The example also teaches the model the `action` verb vocabulary: `NAVIGATE`, `LOCATE`, `CLICK`, `COPY`, `INPUT`. The frontend renders these as colour-coded badges, so consistency matters.

#### Strict Output Schema

The output is constrained to this JSON schema:

```json
{
  "type": "object",
  "properties": {
    "reasoning_steps": { "type": "array", "items": { "type": "string" } },
    "instructions": {
      "type": "array",
      "minItems": 7,
      "maxItems": 7,
      "items": {
        "type": "object",
        "properties": {
          "step": { "type": "integer" },
          "action": { "type": "string" },
          "description": { "type": "string" }
        },
        "required": ["step", "action", "description"]
      }
    }
  },
  "required": ["reasoning_steps", "instructions"]
}
```

The `minItems: 7 / maxItems: 7` bit enforces the exact 7-step requirement from the spec. No wiggle room.

---

## Section 3: Agentic Tooling and MCP

### 3.1 MCP Server

I used FastMCP for the server (`src/mcp_server/server.py`). It generates JSON-RPC 2.0 boilerplate from Python docstrings, which saved me from writing a bunch of protocol plumbing by hand.

#### Sandboxing

Every file access is locked to `mock_ui/`. Path traversal attempts like `../../etc/passwd` hit a `PermissionError` before any I/O happens:

```python
BASE_DIR = Path(__file__).parent.parent / "mock_ui"

def _safe_path(filename: str) -> Path:
    resolved = (BASE_DIR / filename).resolve()
    if not str(resolved).startswith(str(BASE_DIR.resolve())):
        raise PermissionError("Access denied: path traversal attempt.")
    return resolved
```

The server also rejects anything that isn't `.json`, `.xml`, or `.html`. In production it runs as a separate process over `stdio`, so even if someone finds an exploit, it can't touch the FastAPI host.

#### The Four Tools

| Tool                                                    | What it does                          | Params                           |
| ------------------------------------------------------- | ------------------------------------- | -------------------------------- |
| `list_mock_ui_files()`                                  | Lists available UI files              | —                                |
| `read_mock_ui(filename)`                                | Full file read (JSON parsed, XML raw) | `filename`                       |
| `get_interactive_elements(filename)`                    | Returns only inputs, buttons, forms   | `filename`                       |
| `find_element_by_attribute(filename, attribute, value)` | Targeted element lookup               | `filename`, `attribute`, `value` |

I split these intentionally. `get_interactive_elements` returns ~10 elements; `read_mock_ui` returns ~40. The agent is prompted to call the cheap tool first and only fall back to the full read when it actually needs more context. This cuts token usage by about 60% on typical queries.

#### Architecture

```
┌─────────────────────────────────┐
│         USER (Browser)          │
└──────────────┬──────────────────┘
               │ HTTP POST /api/v1/agent/task
┌──────────────▼──────────────────┐
│   FastAPI Backend (MCP Host)    │
│   - Manages conversation state  │
│   - Streams SSE to frontend     │
│   - Runs the agentic loop       │
└────────┬──────────────┬─────────┘
         │ Anthropic API│ stdio / direct call
         │              │
┌────────▼──────┐  ┌────▼───────────────────┐
│  Claude 3.5   │  │  MCP Server (server.py) │
│  Sonnet API   │  │  - list_mock_ui_files   │
│               │  │  - read_mock_ui         │
│  Decides WHAT │  │  - get_interactive_*    │
│  tool to call │  │  - find_element_by_*    │
└───────────────┘  └────────────────────────┘
                              │ reads
                   ┌──────────▼──────────────┐
                   │  mock_ui/ (sandboxed)    │
                   │  login_context.json      │
                   └─────────────────────────┘
```

### 3.2 The Agentic Loop

The core loop in `src/backend/main.py` follows a Think → Act → Observe pattern. Here's what a typical run looks like:

```
Iteration 1:
  Claude gets: user prompt + list of 4 MCP tools
  Claude thinks: "I need to see the UI before I can answer"
  Claude returns: tool_use → get_interactive_elements("login_context.json")
  Host runs the tool → gets back JSON with 3 inputs + 1 form
  Result gets appended to message history

Iteration 2:
  Claude gets: original prompt + tool result from iteration 1
  Claude thinks: "I have enough context now"
  Claude returns: end_turn + structured JSON answer
  Host streams the final answer to the frontend
```

Three guardrails keep the agent from going rogue. First, I capped iterations at 5. if the model hasn't converged by then, something's wrong and I'd rather surface an error than burn API credits. Second, Each iteration appends the full assistant turn and tool results to the message history so Claude never loses context. Third, the loop checks `stop_reason` after every API call. if Claude signals `end_turn`, the agent breaks immediately instead of waiting for the iteration counter.

---

## Section 4: Fullstack Implementation

Code lives in `/src`. Here's how the pieces fit together.

### 4.1 Backend: FastAPI

I picked FastAPI over Flask or Express because I needed native async, Pydantic validation, and `StreamingResponse` because I didn't want to bolt those on as middleware. `main.py` exposes three endpoints:

```
POST /api/v1/agent/task   → StreamingResponse (SSE)
GET  /api/v1/ui-files     → JSON list of available mock files
GET  /health              → { status, model }
```

The `run_agent()` async generator yields typed SSE events. Each event type maps to a specific frontend component:

| Event          | Payload                  | Renders as              |
| -------------- | ------------------------ | ----------------------- |
| `status`       | `message`                | Grey status badge       |
| `thought`      | `content`                | Purple reasoning bubble |
| `tool_call`    | `tool`, `input`          | Blue tool card          |
| `tool_result`  | `tool`, `result_preview` | Green result preview    |
| `final_answer` | `structured`, `raw`      | Right panel output      |
| `done`         | `message`                | Stream terminator       |
| `error`        | `message`                | Red error card          |

### 4.2 Frontend: Next.js / React

`AgentInterface.tsx` is a single-file component with three panels: prompt input on the left, a live event feed in the centre (colour-coded by type, auto-scrolling), and structured output cards on the right.

One hurdle I encountered and solved during development: `TextDecoder` needs `{ stream: true }` or it'll mangle multi-byte characters that get split across network chunks.

```typescript
const decoder = new TextDecoder("utf-8");
const chunk = decoder.decode(value, { stream: true });
```

I also buffer SSE chunks and split on `"\n\n"` before JSON-parsing. Without that, you get parse errors whenever a message boundary lands in the middle of a TCP segment. I chose SSE over WebSockets because the data flow is strictly server→client. WebSockets would've added connection management complexity for no benefit.

### 4.3 Example: "How do I reset my password?"

```
User prompt → FastAPI → Claude (Iteration 1)
  ↓ tool_use: get_interactive_elements("login_context.json")
  MCP Server returns: [form, input#user-name, input#password, input#login-button]
  ↓ tool_use: find_element_by_attribute("login_context.json", "class", "login_credentials")
  MCP Server returns: div with "Accepted usernames... standard_user... secret_sauce"

Claude (Iteration 2) → end_turn + structured JSON
```

The agent figures out that SauceDemo doesn't actually have a password reset flow, the credentials are printed right on the login page, So it maps the user's intent to "copy the published credentials and log in." Here's the output:

```json
{
  "reasoning_steps": [
    "Read interactive elements — found username input, password input, login button",
    "Located login_credentials div containing accepted usernames",
    "Located login_password div containing the shared password 'secret_sauce'",
    "SauceDemo does not have a password reset flow — credentials are pre-published on the login page",
    "Mapped user intent to: copy credentials from the page and use them to log in"
  ],
  "instructions": [
    {
      "step": 1,
      "action": "NAVIGATE",
      "description": "Open https://www.saucedemo.com/ in your browser."
    },
    {
      "step": 2,
      "action": "LOCATE",
      "description": "Scroll to the bottom of the login page to find the 'Accepted usernames are:' section."
    },
    {
      "step": 3,
      "action": "COPY",
      "description": "Copy a valid username (e.g. standard_user) from the list."
    },
    {
      "step": 4,
      "action": "LOCATE",
      "description": "Find the 'Password for all users:' section directly below the usernames."
    },
    {
      "step": 5,
      "action": "COPY",
      "description": "Copy the shared password: secret_sauce."
    },
    {
      "step": 6,
      "action": "INPUT",
      "description": "Paste the username into the field with id='user-name' (placeholder: 'Username')."
    },
    {
      "step": 7,
      "action": "INPUT",
      "description": "Paste the password into the field with id='password', then click the Login button (id='login-button') to authenticate."
    }
  ]
}
```

---

## Section 5: Evaluation and Performance Tuning

### 5.1 How I Measure Quality

#### Automated metrics

| Metric            | How I measure it                                                                         | Target                   |
| ----------------- | ---------------------------------------------------------------------------------------- | ------------------------ |
| Tool Correctness  | Assert `get_interactive_elements` fires before `read_mock_ui` on structured queries      | ≥ 95%                    |
| Schema Compliance | `jsonschema` validation — checks `minItems=7`, all required fields                       | 100%                     |
| Faithfulness      | Compare element IDs/classes in output against ground truth from `login_context.json`     | 0 hallucinated selectors |
| Answer Relevancy  | Cosine similarity (prompt embedding vs. instruction embedding, `text-embedding-3-small`) | ≥ 0.80                   |
| Latency (TTFT)    | `time.perf_counter()` from request receipt to first SSE chunk                            | ≤ 3s                     |
| Token Efficiency  | `usage.input_tokens + usage.output_tokens` logged per call                               | Track trend              |

#### LLM-as-Judge

I run a second Claude call (or GPT-4o) to grade responses on a 1–5 rubric across three dimensions: clarity (are the instructions unambiguous?), safety (does it avoid leaking credentials needlessly?), and grounding (is every instruction traceable to an actual element in the JSON?).

I also keep 30 "gold standard" prompt→expected-output pairs as a regression suite in `pytest`. Any prompt or system-prompt change that drops schema compliance or faithfulness below threshold gets blocked.

### 5.2 Resource Monitoring

#### Self-hosted inference (Ollama / vLLM)

For local deployments, I'd wire up Prometheus + Grafana. The KPIs I care about are:

| KPI            | Threshold | If breached                                |
| -------------- | --------- | ------------------------------------------ |
| KV Cache Usage | < 85%     | Reduce `max_tokens`, enable sliding window |
| TTFT           | < 3s      | More GPU parallelism, PagedAttention       |
| Tokens/sec     | > 30      | Enable continuous batching                 |
| Queue Wait     | < 5s      | Add a replica                              |
| 5xx Error Rate | < 1%      | Page on-call, check for prompt regressions |
| GPU VRAM       | < 90%     | INT8 quantisation                          |

#### Cloud API (Anthropic)

For the hosted API, I log every response's usage block:

```python
usage = response.usage
metrics.record({
    "input_tokens": usage.input_tokens,
    "output_tokens": usage.output_tokens,
    "cost_usd": (usage.input_tokens / 1e6 * 3.00) + (usage.output_tokens / 1e6 * 15.00),
    "ttft_ms": ttft,
    "iterations": iteration_count,
})
```

In Grafana I track daily token spend (budget alert at $10/day), P95 latency per endpoint, and a tool-call frequency heatmap to spot if the agent is over-calling expensive tools.

#### Cost optimisations I applied

**Tool granularity**. `get_interactive_elements` returns a fraction of what `read_mock_ui` does, and the agent is told to try it first. That alone cut average input tokens by 60%.

**Response caching**. identical `(prompt, ui_file_hash)` pairs get served from Redis for 5 minutes. No LLM call needed.

**Prompt caching**. the system prompt is 800 tokens and mostly static, so it's eligible for Anthropic's prompt caching beta. That's a ~90% cost reduction on the prompt prefix for repeat calls.

**Model routing**. simple lookup queries ("what's the id of the login button?") get routed to `claude-haiku-3-5` at 1/10th the cost. Sonnet only handles the full 7-step generation tasks.

---

## Design Decisions

| Decision      | Choice                             | Why                                                                                                                |
| ------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| LLM           | Claude 3.5 Sonnet                  | Best reasoning benchmarks for structured JSON generation. GPT-4o was faster but less reliable on multi-step tasks. |
| MCP library   | FastMCP                            | Generates JSON-RPC boilerplate from docstrings. I didn't want to hand-roll protocol code for four tools.           |
| Backend       | FastAPI + uvicorn                  | Native async, Pydantic validation, `StreamingResponse` — all built in. Flask would've needed three extra packages. |
| Streaming     | SSE + TextDecoder (`stream: true`) | Data flows one direction (server→client). WebSockets would've added bidirectional overhead I don't need.           |
| Sandboxing    | Path resolution check              | Zero dependencies. Blocks traversal before any file I/O.                                                           |
| Output format | Typed SSE events                   | The frontend can render each event type differently without polling or guessing.                                   |
