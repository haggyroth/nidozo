"""ModelBackend protocol — the single interface all LLM providers must satisfy."""

from typing import Protocol, TypedDict


class Message(TypedDict):
    role: str   # "system" | "user" | "assistant"
    content: str


class Usage(TypedDict):
    """Token counts from the most recent completion (for cost analytics, #225)."""

    prompt_tokens: int
    completion_tokens: int


class ModelBackend(Protocol):
    """Complete a conversation and return the assistant's reply as a plain string.

    After each ``complete()`` call, implementations expose that call's token
    usage via ``last_usage`` (None when the provider reports none, e.g. some
    local models). Callers read it defensively with ``getattr`` so stub backends
    that don't track usage still satisfy the duck-typed interface.
    """

    last_usage: Usage | None

    async def complete(self, messages: list[Message]) -> str:
        ...
