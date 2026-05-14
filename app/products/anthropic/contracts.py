from __future__ import annotations

# Public Anthropic Messages contract: minimal, explicit allowlist.
ANTHROPIC_MESSAGES_REQUIRED_FIELDS = {"model", "messages"}
ANTHROPIC_MESSAGES_PUBLIC_FIELDS = ANTHROPIC_MESSAGES_REQUIRED_FIELDS | {
    "max_tokens",
    "system",
    "metadata",
    "output_config",
    "raw_output",
    "thinking",
    "tools",
    "tool_choice",
    "stop_sequences",
    "stream",
    "temperature",
    "top_p",
    "top_k",
    "service_tier",
}
