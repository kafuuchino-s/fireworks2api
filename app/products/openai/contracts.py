from __future__ import annotations

# Sources:
# - OpenAI Chat Completions: https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
# - OpenAI Responses: https://developers.openai.com/api/reference/resources/responses/methods/create/
# Fireworks internal contracts live in app.dataplane.fireworks.contracts.

OPENAI_CHAT_REQUIRED = {"model", "messages"}
OPENAI_CHAT_STANDARD_OPTIONAL = {
    "audio",
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "metadata",
    "modalities",
    "n",
    "parallel_tool_calls",
    "prediction",
    "presence_penalty",
    "reasoning_effort",
    "response_format",
    "seed",
    "service_tier",
    "stop",
    "store",
    "stream",
    "stream_options",
    "temperature",
    "tool_choice",
    "tools",
    "top_logprobs",
    "top_p",
    "user",
    "max_completion_tokens",
}
OPENAI_CHAT_DEPRECATED = {"max_tokens", "functions", "function_call"}
OPENAI_NOT_CHAT = {"input", "max_output_tokens", "previous_response_id", "truncation", "text"}

OPENAI_RESPONSES_REQUIRED = {"model", "input"}
OPENAI_RESPONSES_STANDARD_OPTIONAL = {
    "include",
    "instructions",
    "max_output_tokens",
    "metadata",
    "parallel_tool_calls",
    "previous_response_id",
    "reasoning",
    "store",
    "stream",
    "stream_options",
    "temperature",
    "text",
    "tool_choice",
    "tools",
    "top_p",
    "truncation",
    "user",
}
OPENAI_RESPONSES_DEPRECATED = {"max_tokens", "functions", "function_call"}
OPENAI_NOT_RESPONSES = {"messages", "max_completion_tokens", "response_format", "stop", "service_tier", "n", "logprobs", "top_logprobs"}

OPENAI_COMPLETIONS_REQUIRED = {"model", "prompt"}
OPENAI_COMPLETIONS_OPTIONAL = {"suffix", "max_tokens", "temperature", "top_p", "n", "stream", "logprobs", "echo", "stop", "presence_penalty", "frequency_penalty", "best_of", "logit_bias", "user", "seed"}
OPENAI_COMPLETIONS_DEPRECATED = {"max_tokens"}

OPENAI_EMBEDDINGS_REQUIRED = {"model", "input"}
OPENAI_EMBEDDINGS_OPTIONAL = {"encoding_format", "dimensions", "user", "prompt_template", "return_logits", "normalize"}

OPENAI_RERANK_REQUIRED = {"query", "documents"}
OPENAI_RERANK_OPTIONAL = {"model", "top_n", "rank_fields", "return_documents", "task", "truncate"}

OPENAI_COMPLETIONS_PUBLIC = OPENAI_COMPLETIONS_REQUIRED | OPENAI_COMPLETIONS_OPTIONAL | OPENAI_COMPLETIONS_DEPRECATED
OPENAI_EMBEDDINGS_PUBLIC = OPENAI_EMBEDDINGS_REQUIRED | OPENAI_EMBEDDINGS_OPTIONAL
OPENAI_RERANK_PUBLIC = OPENAI_RERANK_REQUIRED | OPENAI_RERANK_OPTIONAL

OPENAI_CHAT_ALL = OPENAI_CHAT_REQUIRED | OPENAI_CHAT_STANDARD_OPTIONAL | OPENAI_CHAT_DEPRECATED
OPENAI_RESPONSES_ALL = OPENAI_RESPONSES_REQUIRED | OPENAI_RESPONSES_STANDARD_OPTIONAL | OPENAI_RESPONSES_DEPRECATED
