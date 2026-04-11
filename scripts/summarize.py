"""
summarize.py — Local LLM session summariser for claude-recall.

Loads Qwen2.5 0.5B GGUF via llama-cpp-python and produces a structured
JSON summary from a session transcript.

Called by save_context.py after every session. If the model file is absent
or llama-cpp-python is not installed, returns None and save_context.py falls
back to the original regex-based extract_facts().
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import get_model_path, llm_available

# Prompt template — kept short so it fits in a 2048-token context window
_SYSTEM = (
    "You are a senior developer writing a structured session note. "
    "Respond ONLY with valid JSON. No markdown, no explanation."
)

_USER_TEMPLATE = """Read this Claude Code session transcript and produce a JSON summary.

TRANSCRIPT (last {n} turns):
{turns}

Output exactly this JSON structure — fill every field:
{{
  "summary": "2-3 sentence description of what was actually accomplished",
  "decisions": ["decision or finding 1", "decision or finding 2"],
  "files_and_roles": {{"filename.ext": "what this file does / what changed"}},
  "next_steps": ["concrete next step 1", "concrete next step 2"],
  "keywords": ["tag1", "tag2", "tag3"]
}}

Rules:
- summary must describe WHAT was done, not just "we talked about X"
- decisions: real architectural or implementation decisions, not obvious steps
- files_and_roles: only files actually touched or discussed, with their purpose
- next_steps: actionable TODOs that follow directly from this session
- keywords: 3-6 lowercase tags for searching
- If the session was trivial or empty, still return valid JSON with honest short values
"""


def _build_prompt(messages: list[dict]) -> str:
    """Format the last 8 user+assistant turns into a readable transcript string."""
    recent = [m for m in messages if m.get("role") in ("user", "assistant")][-16:]
    lines = []
    for m in recent:
        role = "User" if m["role"] == "user" else "Claude"
        content = str(m.get("content", ""))[:600]  # cap per-message to save tokens
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


def generate_summary(messages: list[dict]) -> dict | None:
    """
    Generate a structured session summary using the local Qwen model.

    Returns a dict with keys: summary, decisions, files_and_roles,
    next_steps, keywords — or None if the model is unavailable or fails.

    The caller (save_context.py) should fall back to extract_facts()
    when this returns None.
    """
    if not llm_available():
        return None

    try:
        from llama_cpp import Llama

        model_path = str(get_model_path())
        llm = Llama(
            model_path=model_path,
            n_ctx=2048,
            n_threads=4,
            n_gpu_layers=0,   # CPU only — no GPU assumption
            verbose=False,
        )

        turns_text = _build_prompt(messages)
        user_msg = _USER_TEMPLATE.format(n=min(8, len(messages)), turns=turns_text)

        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=512,
            temperature=0.1,   # low temp = more consistent JSON
            stop=["```"],
        )

        raw = response["choices"][0]["message"]["content"].strip()

        # Strip markdown code fences if the model added them despite instructions
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw)

        # Validate expected keys are present
        required = {"summary", "decisions", "files_and_roles", "next_steps", "keywords"}
        if not required.issubset(result.keys()):
            return None

        return result

    except Exception as exc:
        print(f"[claude-recall] summarize.py error: {exc}", file=sys.stderr)
        return None