"""
summarize.py — Local LLM session summariser for claude-recall.

Uses Qwen2.5 0.5B GGUF via llama-cpp-python to generate a structured
summary from a session transcript.

DESIGN: Qwen 0.5B is a very small model (490M params). We pre-extract
facts with regex, then ask the LLM to ONLY rephrase/clean them into a
readable summary. This avoids the model echoing prompt templates or
hallucinating content.

Called by save_context.py after every session. If the model is unavailable,
returns None and save_context.py uses regex-based facts as fallback.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import get_model_path, llm_available, get_llm, DEBUG_LOG

from datetime import datetime

def _debug(msg: str) -> None:
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] SUMMARIZE: {msg}\n")
    except Exception:
        pass


# PROMPT DESIGN NOTE:
# Qwen 0.5B (490M params) has a strong tendency to copy/echo example text.
# We CANNOT use few-shot examples — it will copy them verbatim.
# Instead: feed the extracted facts directly and ask for a clean-up rewrite.
_SYSTEM = "You summarize coding sessions. Respond ONLY as JSON."

# This prompt feeds the pre-extracted facts directly.
# The model's job is to rephrase them into readable prose — NOT generate new info.
_PROMPT = """Session facts:
- Task: {first_prompt}
- Files changed: {files}
- Message count: {turns}
{context_line}
Summarize this session as JSON:
{{"summary": "<what was done in 1-2 sentences>", "next_steps": ["<what to do next>"], "keywords": ["<topic1>", "<topic2>"]}}"""


def generate_summary(messages: list[dict], facts: dict | None = None) -> dict | None:
    """Generate a structured session summary using the local Qwen model.

    Args:
        messages: list of {role, content} dicts from transcript
        facts: pre-extracted facts dict (first_prompt, files, turns, etc.)
              If provided, the LLM just rephrases the facts.
              If not provided, extracts from messages.

    Returns dict with keys: summary, next_steps, keywords
    Or None if model unavailable or fails.
    """
    if not llm_available():
        return None

    try:
        llm = get_llm()
        if llm is None:
            return None

        # Pre-extract facts if not provided
        if facts is None:
            facts = _quick_extract(messages)

        first_prompt = facts.get("first_prompt", "unknown task")[:200]
        files = facts.get("files", [])
        files_str = ", ".join(files[:8]) if files else "unknown"
        turns = facts.get("turns", 1)

        # Extract a brief excerpt from assistant messages for more context
        asst_msgs = [m["content"] for m in messages if m.get("role") == "assistant" and isinstance(m.get("content"), str)]
        context_line = ""
        if asst_msgs:
            # Get last assistant message, first line, cleaned
            last_asst = asst_msgs[-1].strip().split("\n")[0][:150]
            last_asst = last_asst.replace('"', "'")
            if len(last_asst) > 20:
                context_line = f"- Last response: {last_asst}"

        # Build the facts-first prompt
        prompt = _PROMPT.format(
            first_prompt=first_prompt,
            files=files_str,
            turns=turns,
            context_line=context_line,
        )

        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=256,
            temperature=0.1,
        )

        # Handle different response formats from llama-cpp-python
        choice = response["choices"][0]
        msg = choice.get("message", choice)
        if isinstance(msg, str):
            raw = msg.strip()
        elif isinstance(msg, dict):
            raw = (msg.get("content") or "").strip()
        else:
            return None

        _debug(f"LLM raw output: {raw[:200]}")

        # Strip markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        raw = raw.strip()

        # Try direct JSON parse first
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # JSON repair for truncated output (common with small models)
            result = _repair_json(raw)
            if result is None:
                _debug(f"JSON parse failed, repair also failed")
                return None

        # Normalize: ensure next_steps and keywords are always lists
        # (Qwen 0.5B sometimes returns them as strings)
        for key in ("next_steps", "keywords"):
            val = result.get(key)
            if isinstance(val, str) and val.strip():
                result[key] = [s.strip() for s in val.split(",") if s.strip()]
            elif not isinstance(val, list):
                result[key] = []

        # Validate — reject prompt echoes
        summary = result.get("summary", "")
        if not isinstance(summary, str) or len(summary) < 10:
            _debug("Rejected: summary too short")
            return None

        # Reject if it echoes the prompt template
        echo_patterns = [
            "what was done in",             # from template placeholder
            "what to do next",              # from template placeholder
            "<topic",                       # from template placeholder
            "respond only",                 # from system prompt
            "session facts:",               # from prompt structure
            "summarize this session",        # from prompt structure
        ]
        summary_lower = summary.lower()
        if any(p in summary_lower for p in echo_patterns):
            _debug(f"Rejected: prompt echo detected in summary: {summary[:80]}")
            return None

        # Ensure required keys
        result.setdefault("next_steps", [])
        result.setdefault("keywords", [])

        # Backward compatibility: add empty optional fields
        result.setdefault("decisions", [])
        result.setdefault("files_and_roles", {})

        _debug(f"LLM summary OK: {summary[:80]}")
        return result

    except json.JSONDecodeError as e:
        _debug(f"JSON parse failed: {e}")
        return None
    except Exception as exc:
        _debug(f"summarize error: {exc}")
        print(f"[claude-recall] summarize.py error: {exc}", file=sys.stderr)
        return None


def _quick_extract(messages: list[dict]) -> dict:
    """Quick regex extraction of facts from messages (no LLM needed)."""
    import re

    user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
    all_text = " ".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str))

    # Files mentioned
    file_re = re.compile(
        r'[\w./\-]+\.(?:tsx?|jsx?|py|dart|go|rs|rb|java|kt|swift|'
        r'md|json|yaml|yml|toml|sh|html|css|scss)\b'
    )
    files = list(dict.fromkeys(m.group() for m in file_re.finditer(all_text)))[:15]

    return {
        "first_prompt": user_msgs[0][:300].replace("\n", " ") if user_msgs else "(no messages)",
        "turns": len(user_msgs),
        "files": files,
    }


def _repair_json(raw: str) -> dict | None:
    """Try to extract a valid JSON object from truncated LLM output.

    Common case: model generates valid JSON but it gets cut off at max_tokens.
    We try to extract at least the "summary" field.
    """
    import re

    # Try to extract summary field with regex
    summary_match = re.search(r'"summary"\s*:\s*"([^"]+)"', raw)
    if not summary_match:
        return None

    summary = summary_match.group(1).strip()
    if len(summary) < 10:
        return None

    result = {"summary": summary, "next_steps": [], "keywords": []}

    # Try to extract next_steps (as array or string)
    steps_match = re.search(r'"next_steps"\s*:\s*\[([^\]]*)\]', raw)
    if steps_match:
        steps_raw = steps_match.group(1)
        result["next_steps"] = [
            s.strip().strip('"').strip("'")
            for s in steps_raw.split(",")
            if s.strip().strip('"').strip("'")
        ]
    else:
        # Handle next_steps as a plain string
        steps_str = re.search(r'"next_steps"\s*:\s*"([^"]+)"', raw)
        if steps_str:
            result["next_steps"] = [
                s.strip() for s in steps_str.group(1).split(",") if s.strip()
            ]

    # Try to extract keywords
    kw_match = re.search(r'"keywords"\s*:\s*\[([^\]]*)\]', raw)
    if kw_match:
        kw_raw = kw_match.group(1)
        result["keywords"] = [
            k.strip().strip('"').strip("'")
            for k in kw_raw.split(",")
            if k.strip().strip('"').strip("'")
        ]

    return result