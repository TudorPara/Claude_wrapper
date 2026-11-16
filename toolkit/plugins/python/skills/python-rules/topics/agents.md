---
name: nexttech-agents
description: How agents are built in a Nexttech service with LangGraph and deepagents. Covers create_deep_agent, filtering built-in tools with middleware, tool names as enum constants, tool-call limits, the orchestrator and its route tools, hand-offs, retrieval, conversation history, streaming deltas, pauses and forms, tool errors, identity and entitlements, and Langfuse tracing. Use it when writing or reviewing an agent, an orchestrator, a tool, a prompt, agent middleware, agent state or a chat stream.
---

# Nexttech agents

A source such as `#13` is a pull request of `nexttech-assistant-svc`. Sections 1 to 4 are reviewers' rulings. Section 5 is carried over from that project's design; a new project's own docs win where they differ.

## 1. Building an agent

- Build agents with `create_deep_agent`, not `create_agent` (#13; #13).
- deepagents adds file, todo and task tools. Filter them with middleware, as the orchestrator does; a sentence in the prompt is not a guardrail (#13; #10).
- An agent's folder is `agents/<name>_agent/` (#14), with `agent.py`, `constants.py`, `context.py`, `prompts.py`, `state.py` and `tools/`.
- An agent's compiled graph is built once, in the lifespan (#22). A compiled graph, an agent and a middleware object are shared by concurrent runs, so they hold configuration only. Counts and remembered results live in the run's state.
- An agent needs the conversation's history, or its own checkpointer per conversation, so a follow-up question makes sense (#13).
- No LangGraph `interrupt`. A pause is checkpoint state plus a stored form (#10).
- Tool-call limits come from one middleware configured per agent, not one middleware per tool (#10). "Same knob, same name": the bound is `max_recursion_limit` everywhere (#10, #13).
- Every loop is bounded by a setting. Reaching the bound returns what was found so far, not an error.
- One turn is one Langfuse trace: pass the trace id to every sub-agent (#7).

## 2. Tools

- Tool names are constants in an enum in the agent's `constants.py`, and every prompt uses the enum instead of retyping the name (#10, #14; #13).
- A tool list is named for its agent, e.g. `CV_REFRESH_AGENT_TOOLS` (#14).
- Tool names carry no `_tool` suffix (#13).
- No nested functions to build a tool (#13).
- A tool is a thin wrapper over a service. A service a tool uses is built once in the lifespan and handed over explicitly; a tool cannot use `Depends`.
- Identity never appears in a tool schema. A tool that reads or writes the caller's own record gets the identifier fixed when the tool is built.
- A tool that fails returns an error the model can explain to the person, e.g. `{"error": ...}`, instead of a raw exception string (#10).
- The vector store reaches tools through the run config, not the context (#13).
- Open: tool file names. The orchestrator's files on `nexttech-assistant-svc` end in `_tool.py`, and #13 asked to drop `_tool` from tool names. Follow the nearest merged code, ask the project lead, and record the answer in `nexttech-project-rulings`.

## 3. The orchestrator

- It has one route tool per agent, all calling `dispatch_route`, and one `invoke_<agent>` per agent (#7).
- It classifies; it does not return a probability (#7).
- Each outcome has its own state field (#7).
- It receives only the agents the caller may use (#7). A sub-agent that is not there cannot be called.
- Two parallel tool calls must work (#7).
- The orchestrator yields the hand-off event itself; a sub-agent must not yield one too (#10).
- It owns routing and nothing else: no retrieval, no domain knowledge, no answering.
- The agent catalog is `agents/agent_catalog.py` (#7).

## 4. Retrieval, streaming and what the person sees

- Do not feed the model every retrieved chunk; it pollutes the context (#13).
- Read known chunks with a filtered scroll, not a similarity search (#13).
- A search carries the caller's document scope as a filter on the search itself, not as a pass over the results.
- Retrieval limits come from settings, under one name in settings and in code: `similarity_score` (#13).
- Sources are assembled from the passages actually retrieved, never from what the model says about where its answer came from.
- Stream the answer as it is written: one delta per model chunk, not one delta at the end (#13).
- Progress steps are sentences shown to the person (#13).
- Forms are A2UI surfaces, not HTML strings (#10).
- Open: the case of stream event values. Reviewers asked for UPPERCASE everywhere (#22); on `nexttech-assistant-svc` they stayed lowercase because the frontend contract used them. Follow the project's frontend contract and ask.

## 5. Carried over from the nexttech-assistant-svc design

- Identity and entitlements are settled before any model runs, and nothing the person typed can change them. Nothing downstream widens them.
- Retrieved text and user input are evidence, never instructions. Model output never grants access and never changes who the current user is. The `context` of a submitted form is validated like any other input.
- Decisions the system acts on are typed: a value from a fixed set. Text a person reads comes from the prose model. Never ask a model for a priority in prose.
- No agent acts for a person without that person confirming it. Nothing is written to an outside system before the confirmation.
- An outside system that is unreachable is reported in the same turn. Nothing is queued or retried behind the person's back.
- An agent yields progress, deltas, at most one payload, and hand-offs. Only the endpoint ends the stream, with exactly one `final` or `error`, never both and never neither. The deltas concatenated are exactly the message's stored content.
- Adding a sub-agent must not require changing an existing one.
- Every agent run is traced in Langfuse. Never ship a debug callback.
