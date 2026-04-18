"""
summarize.py — Session summariser for claude-recall.

Uses Claude API (Anthropic or NVIDIA NIM) to generate structured summary
from session transcript. Falls back to regex if API unavailable.

Supports:
- Anthropic: ANTHROPIC_API_KEY
- NVIDIA NIM: OPENAI_API_KEY + NVIDIA_NIM_BASE_URL
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def llm_available() -> bool:
    """Check if Claude API is available (Anthropic or NVIDIA NIM)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("NVIDIA_NIM_BASE_URL"):
        return True
    return False


def is_nvidia_nim() -> bool:
    """Check if using NVIDIA NIM."""
    return bool(os.environ.get("OPENAI_API_KEY") and os.environ.get("NVIDIA_NIM_BASE_URL"))


def _debug(msg: str) -> None:
    """Write debug message to log file."""
    from datetime import datetime
    try:
        log_path = Path.home() / ".claude-recall" / "debug.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] SUMMARIZE: {msg}\n")
    except Exception:
        pass


def clean_transcript(messages: list[dict]) -> list[dict]:
    """Remove noise from messages before LLM summarization."""
    PREAMBLES = ("i'll", "let me", "i will", "sure", "of course",
                 "i'd be happy", "i can help", "i'm going to", "here's")

    cleaned = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")

        if role not in ("user", "assistant"):
            continue

        if isinstance(content, list):
            continue

        text = content.strip()
        if not text:
            continue

        if role == "user":
            cleaned.append({"role": "user", "content": text[:200]})
        else:
            lower = text.lower()
            if any(lower.startswith(p) for p in PREAMBLES):
                for sep in (". ", ": ", "\n"):
                    idx = text.lower().find(sep, 10)
                    if idx > 0:
                        text = text[idx + len(sep):]
                        break

            sentences = text.rsplit(". ", 1)
            last = sentences[-1] if len(sentences) > 1 else text
            last = last[:300].strip()
            if last:
                cleaned.append({"role": "assistant", "content": last})

    return cleaned[-8:]


def _call_anthropic(system_prompt: str, user_prompt: str) -> str | None:
    """Call Anthropic API."""
    try:
        import anthropic
    except ImportError:
        return None
    
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            temperature=0,
            system=[{"type": "text", "text": system_prompt}],
            messages=[{"type": "user", "text": user_prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        _debug(f"Anthropic API error: {e}")
        return None


def _call_nvidia_nim(system_prompt: str, user_prompt: str) -> str | None:
    """Call NVIDIA NIM (OpenAI-compatible)."""
    try:
        from openai import OpenAI
    except ImportError:
        return None
    
    try:
        client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("NVIDIA_NIM_BASE_URL"),
        )
        response = client.chat.completions.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=400,
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        _debug(f"NVIDIA NIM API error: {e}")
        return None


def generate_summary(messages: list[dict], facts: dict | None = None) -> dict | None:
    """Generate a structured session summary using Claude API.

    Returns dict with keys: summary, next_steps, keywords, decisions, files_and_roles
    Or None if model unavailable or fails.
    """
    if not llm_available():
        return None

    try:
        messages = clean_transcript(messages)

        if facts is None:
            facts = _quick_extract(messages)

        first_prompt = facts.get("first_prompt", "")
        files = facts.get("files", [])
        files_str = ", ".join(files[:8]) if files else "none"

        tool_counts = facts.get("tool_counts", {})

        conv_lines = []
        all_prompts = facts.get("all_prompts", [])
        all_responses = facts.get("all_responses", [])
        if all_prompts:
            for i, prompt in enumerate(all_prompts[:6]):
                conv_lines.append(f"User: {prompt[:150]}")
                if i < len(all_responses) and all_responses[i]:
                    conv_lines.append(f"Assistant: {all_responses[i][:100]}")
        else:
            for m in messages[:12]:
                role = m.get('role', '')
                content = m.get('content', '')
                if isinstance(content, str) and content.strip():
                    conv_lines.append(f"{role.title()}: {content[:150]}")

        conversation = "\n".join(conv_lines) if conv_lines else "(empty session)"

        system_prompt = "You are a developer session summarizer. Extract structured facts from the conversation and output ONLY valid JSON. No markdown fences, no explanation."

        user_prompt = f"""Session facts:
- First prompt: {first_prompt}
- Files mentioned: {files_str}
- Tool usage: {tool_counts}

Last 6 conversation turns:
{conversation}

Output JSON with these keys:
- summary: 1-2 sentences of what was done
- next_steps: list of strings
- keywords: list of tech terms
- decisions: list of architectural decisions made
- files_and_roles: dict of filepath -> one-line purpose

Output JSON only:"""

        if is_nvidia_nim():
            raw = _call_nvidia_nim(system_prompt, user_prompt)
        else:
            raw = _call_anthropic(system_prompt, user_prompt)

        if not raw:
            return None

        _debug(f"LLM raw output: {raw[:200]}")

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        raw = raw.strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = _repair_json(raw)
            if result is None:
                _debug(f"JSON parse failed, repair also failed")
                return None

        for key in ("next_steps", "keywords", "decisions"):
            val = result.get(key)
            if isinstance(val, str) and val.strip():
                result[key] = [s.strip() for s in val.split(",") if s.strip()]
            elif not isinstance(val, list):
                result[key] = []

        if not isinstance(result.get("files_and_roles"), dict):
            result["files_and_roles"] = {}

        summary = result.get("summary", "")
        if not isinstance(summary, str) or len(summary) < 10:
            _debug("Rejected: summary too short")
            return None

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
    user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
    all_text = " ".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str))

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
    """Try to extract a valid JSON object from truncated LLM output."""
    summary_match = re.search(r'"summary"\s*:\s*"([^"]+)"', raw)
    if not summary_match:
        return None

    summary = summary_match.group(1).strip()
    if len(summary) < 10:
        return None

    result = {"summary": summary, "next_steps": [], "keywords": [], "decisions": [], "files_and_roles": {}}

    steps_match = re.search(r'"next_steps"\s*:\s*\[([^\]]*)\]', raw)
    if steps_match:
        steps_raw = steps_match.group(1)
        result["next_steps"] = [
            s.strip().strip('"').strip("'")
            for s in steps_raw.split(",")
            if s.strip().strip('"').strip("'")
        ]
    else:
        steps_str = re.search(r'"next_steps"\s*:\s*"([^"]+)"', raw)
        if steps_str:
            result["next_steps"] = [
                s.strip() for s in steps_str.group(1).split(",") if s.strip()
            ]

    kw_match = re.search(r'"keywords"\s*:\s*\[([^\]]*)\]', raw)
    if kw_match:
        kw_raw = kw_match.group(1)
        result["keywords"] = [
            k.strip().strip('"').strip("'")
            for k in kw_raw.split(",")
            if k.strip().strip('"').strip("'")
        ]

    return result