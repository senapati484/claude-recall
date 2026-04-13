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
import re
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
_SYSTEM_ONE_SHOT = "Extract structured facts from this developer conversation. Output only JSON."

_USER_ONE_SHOT = """Example:
User: Add JWT auth to Express routes
Claude: Added verifyToken middleware to protect /api/user and /api/orders endpoints.

Output: {{"summary": "Added JWT middleware to Express routes, protecting auth endpoints", "next_steps": ["Add refresh token rotation", "Write auth middleware tests"], "keywords": ["jwt", "auth", "express", "middleware"], "decisions": ["Chose HS256 for speed over RS256"], "files_and_roles": {{"middleware/auth.js": "JWT verifyToken middleware", "routes/user.js": "Protected user routes"}}}}

---
Now extract facts from this conversation:
{conversation}

Output JSON only:"""


# Legacy constants (kept for compatibility during transition)
_SYSTEM = _SYSTEM_ONE_SHOT
_PROMPT = _USER_ONE_SHOT


def clean_transcript(messages: list[dict]) -> list[dict]:
    """Remove noise from messages before LLM summarization.

    - Drops non-user/assistant roles
    - Drops tool-use blocks (content as list)
    - Drops assistant preambles ("I'll", "Let me", etc.)
    - Truncates user to 200 chars
    - Keeps only last sentence of assistant, max 300 chars
    - Keeps max 8 messages total
    """
    PREAMBLES = ("i'll", "let me", "i will", "sure", "of course",
                 "i'd be happy", "i can help", "i'm going to", "here's")

    cleaned = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")

        # Skip non-human roles
        if role not in ("user", "assistant"):
            continue

        # Skip tool blocks
        if isinstance(content, list):
            continue

        text = content.strip()
        if not text:
            continue

        if role == "user":
            # Keep full user prompt, truncate to 200
            cleaned.append({"role": "user", "content": text[:200]})
        else:
            # Drop preambles — extract last meaningful sentence
            lower = text.lower()
            if any(lower.startswith(p) for p in PREAMBLES):
                for sep in (". ", ": ", "\n"):
                    idx = text.lower().find(sep, 10)
                    if idx > 0:
                        text = text[idx + len(sep):]
                        break

            # Keep last sentence, max 300 chars
            sentences = text.rsplit(". ", 1)
            last = sentences[-1] if len(sentences) > 1 else text
            last = last[:300].strip()
            if last:
                cleaned.append({"role": "assistant", "content": last})

    # Keep only last 8 messages
    return cleaned[-8:]


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
        # Pre-clean the transcript before LLM sees it
        messages = clean_transcript(messages)

        llm = get_llm()
        if llm is None:
            return None

        # Pre-extract facts if not provided
        if facts is None:
            facts = _quick_extract(messages)

        files = facts.get("files", [])
        files_str = ", ".join(files[:8]) if files else "none"

        # Build actual conversation log for the LLM
        conv_lines = []
        all_prompts = facts.get("all_prompts", [])
        all_responses = facts.get("all_responses", [])
        if all_prompts:
            for i, prompt in enumerate(all_prompts[:6]):
                conv_lines.append(f"User: {prompt[:150]}")
                if i < len(all_responses) and all_responses[i]:
                    conv_lines.append(f"Assistant: {all_responses[i][:100]}")
        else:
            # Fallback: extract from raw messages
            for m in messages[:12]:
                role = m.get('role', '')
                content = m.get('content', '')
                if isinstance(content, str) and content.strip():
                    conv_lines.append(f"{role.title()}: {content[:150]}")

        conversation = "\n".join(conv_lines) if conv_lines else "(empty session)"

        # Build the conversation-aware prompt (one-shot format)
        prompt = _USER_ONE_SHOT.format(conversation=conversation)

        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _SYSTEM_ONE_SHOT},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=300,
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
        for key in ("next_steps", "keywords", "decisions"):
            val = result.get(key)
            if isinstance(val, str) and val.strip():
                result[key] = [s.strip() for s in val.split(",") if s.strip()]
            elif not isinstance(val, list):
                result[key] = []

        # Ensure files_and_roles is always a dict
        if not isinstance(result.get("files_and_roles"), dict):
            result["files_and_roles"] = {}

        # Validate — reject prompt echoes and hallucinations
        summary = result.get("summary", "")
        if not isinstance(summary, str) or len(summary) < 10:
            _debug("Rejected: summary too short")
            return None

        # Reject if it echoes the prompt template
        echo_patterns = [
            "what was done in",
            "what to do next",
            "<topic",
            "respond only",
            "session facts:",
            "summarize this session",
            "conversation log:",
            "1-2 sentences",
        ]
        summary_lower = summary.lower()
        if any(p in summary_lower for p in echo_patterns):
            _debug(f"Rejected: prompt echo detected in summary: {summary[:80]}")
            return None

        # Anti-hallucination: check that key nouns in summary appear in the transcript
        transcript_text = conversation.lower()
        summary_words = [w for w in re.findall(r'\b[a-z]{4,}\b', summary_lower)
                        if w not in {'with', 'from', 'that', 'this', 'they', 'were',
                                    'have', 'been', 'what', 'when', 'where', 'which',
                                    'about', 'their', 'would', 'could', 'should',
                                    'session', 'user', 'asked', 'help', 'some',
                                    'project', 'code', 'file', 'files', 'made',
                                    'added', 'also', 'used', 'using', 'into'}]
        if summary_words:
            match_ratio = sum(1 for w in summary_words if w in transcript_text) / len(summary_words)
            if match_ratio < 0.3:
                _debug(f"Rejected: hallucination detected (match={match_ratio:.0%}): {summary[:80]}")
                return None

        # Ensure required keys
        result.setdefault("next_steps", [])
        result.setdefault("keywords", [])
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