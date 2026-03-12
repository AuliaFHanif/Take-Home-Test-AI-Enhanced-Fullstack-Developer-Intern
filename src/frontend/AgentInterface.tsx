/**
 * AgentInterface.tsx
 * React + Next.js frontend that streams and visualises the AI agent's
 * reasoning chain in real time using Server-Sent Events.
 */

"use client";

import { useState, useRef, useCallback } from "react";

// ── Types ─────────────────────────────────────────────────────────────────────
type EventType = "status" | "thought" | "tool_call" | "tool_result" | "final_answer" | "done" | "error";

interface AgentEvent {
  id: string;
  type: EventType;
  timestamp: string;
  [key: string]: unknown;
}

interface Instruction {
  step: number;
  action: string;
  description: string;
}

interface StructuredAnswer {
  reasoning_steps?: string[];
  instructions?: Instruction[];
}

// ── Colour map for event badges ───────────────────────────────────────────────
const EVENT_STYLES: Record<EventType, { bg: string; label: string; icon: string }> = {
  status:       { bg: "bg-slate-100 text-slate-700",   label: "Status",      icon: "⚙️" },
  thought:      { bg: "bg-purple-50 text-purple-800",  label: "Thought",     icon: "🧠" },
  tool_call:    { bg: "bg-blue-50 text-blue-800",      label: "Tool Call",   icon: "🔧" },
  tool_result:  { bg: "bg-green-50 text-green-800",    label: "Tool Result", icon: "📄" },
  final_answer: { bg: "bg-amber-50 text-amber-900",   label: "Answer",      icon: "✅" },
  done:         { bg: "bg-slate-50 text-slate-500",   label: "Done",        icon: "🏁" },
  error:        { bg: "bg-red-50 text-red-800",        label: "Error",       icon: "❌" },
};

// ── Main Component ────────────────────────────────────────────────────────────
export default function AgentInterface() {
  const [prompt, setPrompt] = useState("How do I reset my password?");
  const [uiFile, setUiFile] = useState("login_context.json");
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [finalAnswer, setFinalAnswer] = useState<StructuredAnswer | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const feedRef = useRef<HTMLDivElement>(null);

  const appendEvent = useCallback((event: AgentEvent) => {
    setEvents((prev) => [...prev, event]);
    // Auto-scroll feed
    setTimeout(() => {
      feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight, behavior: "smooth" });
    }, 50);
  }, []);

  const handleSubmit = async () => {
    if (!prompt.trim() || isLoading) return;

    // Reset state
    setEvents([]);
    setFinalAnswer(null);
    setIsLoading(true);
    abortRef.current = new AbortController();

    try {
      const response = await fetch("http://localhost:8000/api/v1/agent/task", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, ui_file: uiFile }),
        signal: abortRef.current.signal,
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body!.getReader();
      // IMPORTANT: stream:true to correctly handle multi-byte characters split across chunks
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE messages are delimited by double newline
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? ""; // keep incomplete chunk

        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data: ")) continue;
          try {
            const data = JSON.parse(line.slice(6));
            const event: AgentEvent = {
              id: crypto.randomUUID(),
              timestamp: new Date().toLocaleTimeString(),
              ...data,
            };
            appendEvent(event);
            if (data.type === "final_answer" && data.structured) {
              setFinalAnswer(data.structured as StructuredAnswer);
            }
          } catch {
            // Malformed chunk — skip
          }
        }
      }
    } catch (err: unknown) {
      if ((err as Error).name !== "AbortError") {
        appendEvent({
          id: crypto.randomUUID(),
          type: "error",
          timestamp: new Date().toLocaleTimeString(),
          message: String(err),
        });
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
    setIsLoading(false);
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4">
        <h1 className="text-xl font-semibold tracking-tight">
          🤖 AI UI Analysis Agent
        </h1>
        <p className="text-sm text-gray-400 mt-0.5">
          Powered by Claude 3.5 Sonnet + MCP
        </p>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Left panel: input */}
        <div className="w-80 border-r border-gray-800 p-5 flex flex-col gap-4 shrink-0">
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">
              UI File (mock)
            </label>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={uiFile}
              onChange={(e) => setUiFile(e.target.value)}
              placeholder="login_context.json"
            />
          </div>

          <div className="flex-1">
            <label className="block text-xs font-medium text-gray-400 mb-1">
              User Prompt
            </label>
            <textarea
              className="w-full h-32 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="How do I reset my password?"
            />
          </div>

          <div className="flex gap-2">
            <button
              onClick={handleSubmit}
              disabled={isLoading}
              className="flex-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded px-4 py-2 transition-colors"
            >
              {isLoading ? "Running…" : "Run Agent"}
            </button>
            {isLoading && (
              <button
                onClick={handleStop}
                className="bg-red-700 hover:bg-red-600 text-white text-sm rounded px-3 py-2 transition-colors"
              >
                Stop
              </button>
            )}
          </div>

          {/* Quick prompts */}
          <div>
            <p className="text-xs text-gray-500 mb-2">Quick prompts:</p>
            <div className="flex flex-col gap-1.5">
              {[
                "How do I reset my password?",
                "Find the login button",
                "What credentials can I use to log in?",
                "List all interactive elements",
              ].map((p) => (
                <button
                  key={p}
                  onClick={() => setPrompt(p)}
                  className="text-left text-xs text-blue-400 hover:text-blue-300 transition-colors"
                >
                  → {p}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Centre: agent thought stream */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-800 text-xs text-gray-500">
            Agent reasoning stream
          </div>
          <div
            ref={feedRef}
            className="flex-1 overflow-y-auto p-5 space-y-3"
          >
            {events.length === 0 && (
              <p className="text-gray-600 text-sm text-center mt-16">
                Submit a prompt to start the agent.
              </p>
            )}
            {events.map((ev) => {
              const style = EVENT_STYLES[ev.type] ?? EVENT_STYLES.status;
              return (
                <div key={ev.id} className={`rounded-lg p-3 ${style.bg}`}>
                  <div className="flex items-center gap-2 mb-1">
                    <span>{style.icon}</span>
                    <span className="text-xs font-semibold uppercase tracking-wide">
                      {style.label}
                    </span>
                    <span className="text-xs opacity-50 ml-auto">{ev.timestamp}</span>
                  </div>
                  {/* Render relevant field per event type */}
                  {ev.type === "tool_call" && (
                    <div>
                      <p className="text-xs font-mono">
                        <strong>{ev.tool as string}</strong>({JSON.stringify(ev.input)})
                      </p>
                    </div>
                  )}
                  {ev.type === "tool_result" && (
                    <p className="text-xs font-mono whitespace-pre-wrap opacity-80">
                      {ev.result_preview as string}
                    </p>
                  )}
                  {(ev.type === "thought" || ev.type === "status" || ev.type === "done") && (
                    <p className="text-sm whitespace-pre-wrap">
                      {(ev.content ?? ev.message) as string}
                    </p>
                  )}
                  {ev.type === "error" && (
                    <p className="text-sm">{ev.message as string}</p>
                  )}
                  {ev.type === "final_answer" && !finalAnswer && (
                    <p className="text-sm whitespace-pre-wrap">{ev.raw as string}</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Right panel: structured answer */}
        <div className="w-96 border-l border-gray-800 flex flex-col overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-800 text-xs text-gray-500">
            Structured output
          </div>
          <div className="flex-1 overflow-y-auto p-5">
            {!finalAnswer ? (
              <p className="text-gray-600 text-sm text-center mt-16">
                Structured output will appear here once the agent finishes.
              </p>
            ) : (
              <div className="space-y-5">
                {/* Reasoning steps */}
                {finalAnswer.reasoning_steps && (
                  <div>
                    <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-2">
                      Reasoning
                    </h3>
                    <ol className="space-y-1">
                      {finalAnswer.reasoning_steps.map((step, i) => (
                        <li key={i} className="text-sm text-gray-300 flex gap-2">
                          <span className="text-gray-600 shrink-0">{i + 1}.</span>
                          {step}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}

                {/* Step-by-step instructions */}
                {finalAnswer.instructions && (
                  <div>
                    <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3">
                      Instructions ({finalAnswer.instructions.length} steps)
                    </h3>
                    <div className="space-y-3">
                      {finalAnswer.instructions.map((instr) => (
                        <div
                          key={instr.step}
                          className="flex gap-3 bg-gray-900 rounded-lg p-3"
                        >
                          <div className="shrink-0 w-6 h-6 rounded-full bg-blue-600 flex items-center justify-center text-xs font-bold">
                            {instr.step}
                          </div>
                          <div>
                            <span className="text-xs font-mono text-blue-400">
                              {instr.action}
                            </span>
                            <p className="text-sm text-gray-200 mt-0.5">
                              {instr.description}
                            </p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}