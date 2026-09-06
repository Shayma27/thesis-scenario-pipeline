"""
OpenAI-compatible LLM client for the vLLM endpoint (TU Berlin HPC).

Serves Meta Llama 3.1 8B Instruct via vLLM's OpenAI-compatible API.
Configuration is read entirely from environment variables — no secrets
are hardcoded here.
"""

from __future__ import annotations

import os

from openai import OpenAI

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://gpu026:8000/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "EMPTY")
MODEL = os.environ.get("LLM_MODEL", "llama31")


def get_client() -> OpenAI:
    """Return a new OpenAI SDK client pointed at the vLLM endpoint."""
    return OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)


def message_to_dict(message) -> dict:
    """
    Convert an OpenAI SDK ChatCompletionMessage into a plain dict so it can
    be appended back into a `messages` list for the next API call.
    Preserves tool_calls when present.
    """
    msg_dict: dict = {"role": message.role, "content": message.content}
    if getattr(message, "tool_calls", None):
        msg_dict["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in message.tool_calls
        ]
    return msg_dict
