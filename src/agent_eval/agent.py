"""The agent: a simple loop around a language model.

1. Send the conversation and the list of tools to the model.
2. If the model asks to use a tool, run it and add the result to the conversation.
3. Repeat until the model gives a final answer, or stop after `max_steps`.

That's all a "tool-using agent" is. The model never runs anything itself;
our code decides what each tool actually does.
"""

from __future__ import annotations

import json
import os
import time

from .defenses import system_prompt, wrap_tool_result
from .tools import TOOL_SPECS, ToolBox


class OpenAIChat:
    """Talks to any OpenAI-compatible server: Ollama (local, free), OpenAI, vLLM, etc."""

    def __init__(self, base_url: str, model: str, api_key_env: str | None = None,
                 temperature: float = 0.0, timeout: float = 120):
        from openai import OpenAI  # imported here so the tests run without a server

        key = os.environ.get(api_key_env, "") if api_key_env else ""
        self.client = OpenAI(base_url=base_url, api_key=key or "not-needed", timeout=timeout)
        self.model = model
        self.temperature = temperature

    def __call__(self, messages: list[dict], tools: list[dict], seed: int | None = None) -> dict:
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, tools=tools,
            temperature=self.temperature, seed=seed,
        )
        msg = resp.choices[0].message
        calls = [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}"}
            for tc in (msg.tool_calls or [])
        ]
        return {"content": msg.content or "", "tool_calls": calls}


def run_agent(chat, user_task: str, toolbox: ToolBox, defense: str,
              max_steps: int = 8, seed: int | None = None) -> dict:
    """Run one task. `chat` is any function (messages, tools, seed) -> {"content", "tool_calls"}."""
    messages = [
        {"role": "system", "content": system_prompt(defense)},
        {"role": "user", "content": user_task},
    ]
    start = time.time()
    final, error, steps = "", None, 0
    try:
        for steps in range(1, max_steps + 1):
            reply = chat(messages, TOOL_SPECS, seed=seed)
            if not reply["tool_calls"]:
                final = reply["content"]
                break
            messages.append({
                "role": "assistant",
                "content": reply["content"] or None,
                "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in reply["tool_calls"]
                ],
            })
            for c in reply["tool_calls"]:
                try:
                    args = json.loads(c["arguments"]) if isinstance(c["arguments"], str) else dict(c["arguments"])
                except json.JSONDecodeError:
                    args = {"_raw": c["arguments"]}
                result = toolbox.call(c["name"], args, step=steps)
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": wrap_tool_result(defense, result)})
        else:
            error = "step_limit"
    except Exception as exc:  # network errors, malformed replies, etc.
        error = f"{type(exc).__name__}: {exc}"
    return {
        "final_answer": final,
        "tool_log": toolbox.log,
        "steps": steps,
        "error": error,
        "seconds": round(time.time() - start, 2),
        "messages": messages,
    }
