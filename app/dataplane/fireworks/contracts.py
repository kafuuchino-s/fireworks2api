from __future__ import annotations

# Fireworks internal inference contracts.
# Sources are cached under docs/fireworks/ and originate from https://docs.fireworks.ai/llms.txt.

INFERENCE_BASE_CATEGORY = "inference"
MANAGEMENT_BASE_CATEGORY = "management"

FIREWORKS_INFERENCE_PATHS = {
    "chat_completions": "chat/completions",
    "completions": "completions",
    "responses": "responses",
    "responses_lifecycle": "responses",
    "embeddings": "embeddings",
    "rerank": "rerank",
    "anthropic_messages": "messages",
    "models": "models",
}


def canonical_inference_path(endpoint: str) -> str:
    canonical = {
        "chat_completions": "v1/chat/completions",
        "completions": "v1/completions",
        "responses": "v1/responses",
        "responses_lifecycle": "v1/responses",
        "embeddings": "v1/embeddings",
        "rerank": "v1/rerank",
        "anthropic_messages": "v1/messages",
        "models": "v1/models",
    }
    return canonical[endpoint]

FIREWORKS_CHAT_SUPPORTED_FIELDS = {
    "context_length_exceeded_behavior",
    "echo",
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "max_tokens",
    "messages",
    "metadata",
    "min_p",
    "model",
    "n",
    "parallel_tool_calls",
    "perf_metrics_in_response",
    "presence_penalty",
    "prompt_cache_isolation_key",
    "prompt_cache_key",
    "raw_output",
    "reasoning_effort",
    "reasoning_history",
    "repetition_penalty",
    "response_format",
    "seed",
    "service_tier",
    "stop",
    "stream",
    "stream_options",
    "temperature",
    "thinking",
    "tool_choice",
    "tools",
    "top_k",
    "top_logprobs",
    "top_p",
    "typical_p",
    "user",
}
FIREWORKS_CHAT_EXTENSION_FIELDS = {
    "context_length_exceeded_behavior",
    "echo",
    "min_p",
    "perf_metrics_in_response",
    "prompt_cache_isolation_key",
    "prompt_cache_key",
    "raw_output",
    "reasoning_history",
    "repetition_penalty",
    "thinking",
    "top_k",
    "typical_p",
}

FIREWORKS_RESPONSES_SUPPORTED_FIELDS = {
    "include",
    "input",
    "instructions",
    "max_output_tokens",
    "max_tool_calls",
    "metadata",
    "model",
    "parallel_tool_calls",
    "perf_metrics_in_response",
    "previous_response_id",
    "prompt_cache_isolation_key",
    "prompt_cache_key",
    "reasoning",
    "store",
    "stream",
    "temperature",
    "text",
    "tool_choice",
    "tools",
    "top_p",
    "truncation",
    "user",
}
FIREWORKS_RESPONSES_EXTENSION_FIELDS = {
    "perf_metrics_in_response",
    "prompt_cache_isolation_key",
    "prompt_cache_key",
}

FIREWORKS_COMPLETIONS_SUPPORTED_FIELDS = {
    "context_length_exceeded_behavior",
    "echo",
    "echo_last",
    "frequency_penalty",
    "ignore_eos",
    "images",
    "logit_bias",
    "logprobs",
    "max_completion_tokens",
    "max_tokens",
    "metadata",
    "min_p",
    "mirostat_lr",
    "mirostat_target",
    "model",
    "n",
    "perf_metrics_in_response",
    "prediction",
    "presence_penalty",
    "prompt",
    "prompt_cache_isolation_key",
    "prompt_cache_key",
    "raw_output",
    "reasoning_effort",
    "reasoning_history",
    "repetition_penalty",
    "response_format",
    "return_token_ids",
    "seed",
    "service_tier",
    "speculation",
    "stop",
    "stream",
    "temperature",
    "thinking",
    "top_k",
    "top_logprobs",
    "top_p",
    "typical_p",
    "user",
}
FIREWORKS_COMPLETIONS_EXTENSION_FIELDS = {
    "context_length_exceeded_behavior",
    "echo_last",
    "ignore_eos",
    "images",
    "max_completion_tokens",
    "metadata",
    "min_p",
    "mirostat_lr",
    "mirostat_target",
    "perf_metrics_in_response",
    "prediction",
    "prompt_cache_isolation_key",
    "prompt_cache_key",
    "raw_output",
    "reasoning_effort",
    "reasoning_history",
    "repetition_penalty",
    "response_format",
    "return_token_ids",
    "service_tier",
    "speculation",
    "thinking",
    "top_k",
    "top_logprobs",
    "typical_p",
}

FIREWORKS_EMBEDDINGS_SUPPORTED_FIELDS = {
    "dimensions",
    "input",
    "model",
    "normalize",
    "prompt_template",
    "return_logits",
}
FIREWORKS_EMBEDDINGS_EXTENSION_FIELDS = {"normalize", "prompt_template", "return_logits"}

FIREWORKS_RERANK_SUPPORTED_FIELDS = {"documents", "model", "query", "return_documents", "task", "top_n"}
FIREWORKS_RERANK_EXTENSION_FIELDS = {"task"}

FIREWORKS_ANTHROPIC_MESSAGES_SUPPORTED_FIELDS = {
    "max_tokens",
    "messages",
    "metadata",
    "model",
    "output_config",
    "raw_output",
    "service_tier",
    "stop_sequences",
    "stream",
    "system",
    "temperature",
    "thinking",
    "tool_choice",
    "tools",
    "top_k",
    "top_p",
}
FIREWORKS_ANTHROPIC_MESSAGES_EXTENSION_FIELDS = {"output_config", "raw_output"}

OPENAI_TO_FIREWORKS_CHAT_FIELDS = {"max_completion_tokens": "max_tokens"}
OPENAI_TO_FIREWORKS_RESPONSES_FIELDS = {"max_tokens": "max_output_tokens"}
