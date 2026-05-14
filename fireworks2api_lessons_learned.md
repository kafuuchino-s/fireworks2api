# fireworks2api Lessons Learned

日期：2026-05-06

这份文档给 `fireworks2api` 项目使用，重点记录我们在 Sub2API + fireworks-shim + Fireworks 官方 API 测试中得到的工程经验和设计依据。

它不是流水账日志，而是后续实现 `fireworks2api` 时应该直接吸收的结论。

## 1. 最重要结论

### 1.1 Fireworks 官方 Responses cache 现在正常

实测 Fireworks 官方：

```text
POST https://api.fireworks.ai/inference/v1/responses
```

现在已经能返回：

```text
usage.input_tokens_details.cached_tokens
```

同一个 Fireworks key、同一个长稳定 prompt 连续请求时：

```text
第一次 cached_tokens = 0
第二次 cached_tokens > 0
```

Kimi K2.6 和 DeepSeek V4 Pro 都已验证。

设计含义：

```text
普通模型和 fast 模型不必为了缓存强制转 Chat Completions。
默认可以走官方 /v1/responses。
```

### 1.2 Fireworks Chat Completions cache 也正常

实测：

```text
POST https://api.fireworks.ai/inference/v1/chat/completions
```

返回：

```text
usage.prompt_tokens_details.cached_tokens
```

设计含义：

```text
当必须使用 Chat Completions 参数时，比如 service_tier=priority，仍然可以保留 prompt cache。
```

### 1.3 不同 Fireworks API key 之间缓存不共享

这是 key pool 设计里最重要的事实。

实测方式：

- key A 使用某个全新长 prompt 请求两次。
- key A 第二次命中 cache。
- key B 使用同一个 prompt、同一个 `prompt_cache_key`、同一个 `user`、同一个 `x-session-affinity` 第一次请求。

结果：

```text
key A 第二次 cached_tokens > 0
key B 第一次 cached_tokens = 0
```

进一步确认：

```text
key B 自己第二次请求会命中自己的缓存。
```

设计含义：

```text
key pool 不能 random / round-robin。
必须 sticky routing。
同一个 stable_key + model 应尽量固定到同一个 Fireworks key。
```

### 1.4 Fast Mode 和 Priority tier 是两回事

Fast Mode：

```text
换 model/router id
```

示例：

```text
kimi-k2.6-turbo -> accounts/fireworks/routers/kimi-k2p6-turbo
glm-5.1-fast -> accounts/fireworks/routers/glm-5p1-fast
```

Priority tier：

```json
{
  "service_tier": "priority"
}
```

Fireworks 文档说明：

```text
service_tier=priority 适用于 OpenAI-compatible chat completions only
```

设计含义：

```text
-fast 可以走 /v1/responses。
-priority 应走 /v1/chat/completions。
-fast-priority 应走 fast router id + /v1/chat/completions + service_tier=priority。
```

### 1.5 Responses 和 Chat Completions 各有优势

Responses 优势：

```text
store
previous_response_id
GET /responses
GET /responses/{id}
DELETE /responses/{id}
status
incomplete_details
更接近 OpenAI Responses 语义
```

Chat Completions 优势：

```text
service_tier
prompt_cache_key
prompt_cache_isolation_key
context_length_exceeded_behavior
reasoning_effort
thinking
reasoning_history
response_format
logprobs
top_logprobs
top_k
min_p
typical_p
repetition_penalty
raw_output
return_token_ids
perf_metrics_in_response
更多 Fireworks 扩展参数
```

设计含义：

```text
fireworks2api 不应该只绑定一种上游接口。
应该按模型别名/请求参数选择 Responses 或 Chat Completions。
```

## 2. 推荐路由设计

### 2.1 普通模型

示例：

```text
kimi-k2.6
glm-5.1
deepseek-v4-pro
MiniMax-M2.7
```

推荐：

```text
客户端 /v1/responses -> Fireworks /v1/responses
客户端 /v1/chat/completions -> Fireworks /v1/chat/completions
```

如果客户端调用 Responses，默认不要转 Chat。

原因：

```text
官方 Responses cache 已正常。
链路更短。
Responses 语义更完整。
```

### 2.2 fast 模型

示例：

```text
kimi-k2.6-turbo -> accounts/fireworks/routers/kimi-k2p6-turbo
glm-5.1-fast -> accounts/fireworks/routers/glm-5p1-fast
```

推荐：

```text
/v1/responses -> Fireworks /v1/responses + fast router model id
```

原因：

```text
fast 是 model/router id，不是 Chat-only 参数。
```

### 2.3 priority 模型

示例：

```text
kimi-k2.6-priority
glm-5.1-priority
deepseek-v4-pro-priority
```

推荐：

```text
/v1/responses -> 内部转换为 Chat Completions -> Fireworks /v1/chat/completions + service_tier=priority
```

原因：

```text
Fireworks 文档说明 priority tier 适用于 OpenAI-compatible chat completions only。
```

### 2.4 fast-priority 模型

示例：

```text
kimi-k2.6-turbo-priority
glm-5.1-fast-priority
```

推荐：

```text
/v1/responses -> 内部转换为 Chat Completions
             -> Fireworks /v1/chat/completions
             -> fast router model id
             -> service_tier=priority
```

设计含义：

```text
fast 和 priority 可以叠加，但二者机制不同。
```

## 3. Key Pool 设计经验

### 3.1 不要 round-robin

不要这样：

```text
request 1 -> key A
request 2 -> key B
request 3 -> key C
```

原因：

```text
Fireworks cache 不跨 key 共享。
同一会话轮询 key 会反复冷启动 cache。
更慢、更贵、cache hit ratio 更低。
```

### 3.2 必须 sticky routing

推荐选择 key 的输入：

优先级：

```text
1. body.prompt_cache_key
2. body.user
3. headers.x-session-affinity
4. headers.x-multi-turn-session-id
5. headers.session_id
6. headers.conversation_id
7. derived stable key
```

derived stable key 可以基于：

```text
model
instructions
tools
tool_choice
stable input prefix
```

不要包含：

```text
随机 request id
时间戳
nonce
最后一条用户问题全文
会频繁变化的统计字段
```

### 3.3 推荐 hash 算法

推荐：

```text
rendezvous hash / highest-random-weight hash
```

原因：

```text
key pool 增删 key 时，只有少部分 stable_key 迁移。
比简单 hash % N 更稳定。
```

hash 输入建议：

```text
stable_key + upstream_model
```

原因：

```text
同一个 stable_key 在不同模型上不应强求同 key。
模型不同，本身也不会共享 KV cache。
```

### 3.4 Failover 只在异常时发生

同一个 stable_key 正常情况下必须稳定落到同一个 key。

只有这些情况才 failover：

```text
429
concurrency limit
quota exceeded
account suspended
payment / billing / PRECONDITION_FAILED
5xx
network timeout
connection error
```

不要因为：

```text
一次请求结束
普通 200 响应
cache miss
输出 token 多
```

就切 key。

### 3.5 Cooldown 策略

建议：

```text
429 / concurrency limit: 30-120 秒 cooldown
5xx: 10-30 秒 cooldown
network timeout: 10-30 秒 cooldown
quota / suspended / payment failed: 长 cooldown 或 disabled
```

状态字段：

```text
name
fingerprint
enabled
cooldown_until
disabled_reason
last_error_type
last_error_at
recent_success_count
recent_failure_count
recent_input_tokens
recent_output_tokens
recent_cached_tokens
cache_hit_ratio
```

### 3.6 Key fingerprint

永远不要在日志、API、WebUI 显示完整 key。

推荐：

```text
sha256(key)[:12]
```

或 key name。

## 4. Cache 设计经验

### 4.1 上游请求应该尽量带这些字段

```text
prompt_cache_key
user
x-session-affinity
```

推荐：

```text
prompt_cache_key = stable_key
user = stable_key 或原始 user
x-session-affinity = stable_key
```

如果用户显式提供 `prompt_cache_key`，优先尊重用户值。

### 4.2 Stable prefix 很重要

高 cache hit ratio 需要：

```text
长稳定内容放前面
动态问题放最后
工具定义稳定
系统提示词稳定
模型 ID 稳定
key 稳定
```

会破坏缓存的因素：

```text
每次请求的时间戳
随机 request id
动态工具顺序
不同客户端 tools schema 抖动
同一会话切换模型
同一会话切换 key
```

### 4.3 Cache metrics 统一口径

Responses usage：

```text
usage.input_tokens
usage.output_tokens
usage.input_tokens_details.cached_tokens
```

Chat Completions usage：

```text
usage.prompt_tokens
usage.completion_tokens
usage.prompt_tokens_details.cached_tokens
```

fireworks2api 内部应归一化为：

```text
input_tokens
output_tokens
cached_tokens
total_tokens
cache_hit_ratio = cached_tokens / input_tokens
```

注意：

```text
不同 API 的 input_tokens 语义可能是否包含 cached tokens，需要按 Fireworks 实际返回统一统计说明。
```

## 5. 参数映射经验

### 5.1 reasoning

OpenAI Responses 风格：

```json
{
  "reasoning": {
    "effort": "low"
  }
}
```

Fireworks Chat Completions 风格：

```json
{
  "reasoning_effort": "low"
}
```

映射：

| 输入 | 输出 |
|---|---|
| `reasoning.effort` | `reasoning_effort` |
| `reasoning.reasoning_effort` | `reasoning_effort` |
| `reasoning` 字符串 | `reasoning_effort` |
| `reasoning_effort` | 原样透传 |

经验：

```text
Fireworks Chat Completions 拒绝 body.reasoning。
GLM 5.1 支持 reasoning_effort=low/none。
MiniMax M2 不接受 reasoning_effort=none，接受 low/medium/high。
thinking 和 reasoning_effort 不要同时发送。
```

### 5.2 truncation

OpenAI Responses：

```json
{
  "truncation": "auto"
}
```

Fireworks Chat：

```json
{
  "context_length_exceeded_behavior": "truncate"
}
```

OpenAI Responses：

```json
{
  "truncation": "disabled"
}
```

Fireworks Chat：

```json
{
  "context_length_exceeded_behavior": "error"
}
```

规则：

```text
如果用户显式传 context_length_exceeded_behavior，则不要覆盖。
```

### 5.3 service_tier

Priority tier：

```json
{
  "service_tier": "priority"
}
```

经验：

```text
Fireworks 接受 service_tier=priority。
Fireworks response body/header 不 echo service_tier。
Sub2API 是从 inbound request 记录 service_tier。
```

设计：

```text
fireworks2api 应在 request log 中记录实际发送的 service_tier。
不要依赖上游 response 返回 service_tier。
```

### 5.4 prompt_cache_key

Chat Completions 文档明确支持：

```text
prompt_cache_key
prompt_cache_isolation_key
```

Responses 文档未明确列出，但实测传 `prompt_cache_key` 不影响缓存，且 Responses 正常返回 cached_tokens。

设计：

```text
Responses 和 Chat 请求都可以尝试传 prompt_cache_key。
如果上游未来严格校验导致错误，需要可配置关闭。
```

## 6. Model mapping 经验

建议默认映射：

```text
kimi-k2.6 -> accounts/fireworks/models/kimi-k2p6
kimi-k2.6-turbo -> accounts/fireworks/routers/kimi-k2p6-turbo
glm-5.1 -> accounts/fireworks/models/glm-5p1
glm-5.1-fast -> accounts/fireworks/routers/glm-5p1-fast
deepseek-v4-pro -> accounts/fireworks/models/deepseek-v4-pro
MiniMax-M2.7 -> accounts/fireworks/models/minimax-m2p7
```

已测试 DeepSeek V4 fast router 猜测均不可用：

```text
accounts/fireworks/routers/deepseek-v4-pro-fast
accounts/fireworks/routers/deepseek-v4-fast
accounts/fireworks/routers/deepseek-v4-pro-turbo
accounts/fireworks/routers/deepseek-v4-turbo
```

设计：

```text
不要凭空生成 Fireworks router id。
fast router 应来自文档、/models 列表或用户配置。
```

## 7. WebUI 应重点展示什么

WebUI 不应该只是 key 管理页面，而应该帮助用户判断是否真的“更快、更便宜、更稳定”。

### 7.1 Dashboard

关键指标：

```text
request_count
error_count
input_tokens
output_tokens
cached_tokens
cache_hit_ratio
average_latency_ms
healthy_key_count
cooldown_key_count
disabled_key_count
```

### 7.2 Key Pool 页面

每个 key 展示：

```text
name
fingerprint
enabled
cooldown_until
disabled_reason
last_error_type
recent request count
recent error count
recent cached_tokens
cache_hit_ratio
```

### 7.3 Cache 分析页面

必须能看：

```text
按 model 的 cache hit ratio
按 key 的 cache hit ratio
按 stable_key hash 的 cache hit ratio
同一 stable_key 是否落到多个 key
同一 model alias 是否映射到多个 upstream model
```

这些能直接发现：

```text
路由太分散
key 太多导致缓存冷启动
模型别名混乱
fast/priority 成本异常
```

### 7.4 请求日志页面

记录：

```text
timestamp
request_id
endpoint
model_alias
upstream_model
selected_key_fingerprint
stable_key_hash
stream
service_tier
input_tokens
output_tokens
cached_tokens
latency_ms
status_code
error_type
```

不要记录：

```text
完整 prompt
完整 Authorization header
完整 Fireworks key
```

## 8. 不要做什么

### 8.1 不要随机轮询 key

这是最重要的反模式。

原因：

```text
Fireworks cache 不跨 key，共享 prompt 也不会跨 key 命中。
```

### 8.2 不要默认所有请求 priority

原因：

```text
priority 成本更高。
Fireworks 不在 response 中 echo service_tier。
Sub2API/fireworks2api 必须自己记录请求是否用了 priority。
```

### 8.3 不要把 fast 和 priority 混为一谈

fast：

```text
model/router id
```

priority：

```text
service_tier=priority
```

二者可以叠加，但不是同一个东西。

### 8.4 不要记录完整 prompt/body

原因：

```text
可能泄露项目代码、用户输入、密钥、私有上下文。
```

默认只记录 metadata 和 usage。

### 8.5 不要打印完整 key

任何地方都不要：

```text
stdout
error message
WebUI
API response
request log
SQLite 查询结果页面
```

只显示：

```text
key name
fingerprint
```

### 8.6 不要凭空猜 router id

DeepSeek V4 的多个 fast router 猜测都 404。

设计：

```text
router id 必须来自官方文档、模型列表或用户配置。
```

## 9. 安全经验

### 9.1 Key 存储

如果第一版用 SQLite 存 key：

```text
DB 文件权限必须限制。
WebUI/API 不返回完整 key。
日志不打印完整 key。
```

后续可以加：

```text
SECRET_KEY 加密 key
```

### 9.2 Admin 接口

必须有：

```text
ADMIN_TOKEN
```

如果没有 ADMIN_TOKEN：

```text
禁用写操作
只允许有限 health/status
```

### 9.3 Debug 模式

默认关闭。

如果开启：

```text
明确提示可能记录敏感数据。
最好仍然不记录完整 key。
```

## 10. 测试经验

fireworks2api 至少应有这些测试脚本或测试用例。

### 10.1 单 key cache

```text
同一个 key
同一个长 prompt
连续两次请求
第二次 cached_tokens > 0
```

### 10.2 跨 key cache

```text
key A 预热
key B 第一次请求同一 prompt
key B cached_tokens 应为 0
```

这个测试证明：

```text
key pool 必须 sticky routing
```

### 10.3 Sticky routing

```text
同一 stable_key + model 多次请求
selected key fingerprint 必须一致
```

### 10.4 Failover

模拟：

```text
429
5xx
quota/suspended
network timeout
```

验证：

```text
selected key 进入 cooldown/disabled
请求切换到下一个健康 key
```

### 10.5 priority

```text
model alias: xxx-priority
endpoint: /v1/responses
upstream: /v1/chat/completions
service_tier: priority
```

### 10.6 fast

```text
model alias: xxx-fast
upstream model: Fireworks router id
不自动加 service_tier
```

### 10.7 fast-priority

```text
model alias: xxx-fast-priority
upstream: /v1/chat/completions
upstream model: Fireworks router id
service_tier: priority
```

### 10.8 Streaming usage

Responses streaming terminal event 可能是：

```text
response.completed
response.incomplete
```

usage 可能在 terminal event 的：

```text
response.usage
```

测试时不要只匹配 `response.completed`，也要处理 `response.incomplete`。

## 11. 从当前 fireworks-shim 可复用的实现经验

当前 shim 已验证可用的设计：

```text
derive_stable_key
prompt_cache_key 注入
x-session-affinity 注入
reasoning.effort -> reasoning_effort
truncation -> context_length_exceeded_behavior
Fireworks extra_body passthrough
stream cached tokens patch
Sub2API probe model alias
usage/cached_tokens logging
```

但 fireworks2api 不一定要继续依赖 LiteLLM。

新的 fireworks2api 可以考虑直接实现：

```text
/v1/responses -> Fireworks /v1/responses
/v1/chat/completions -> Fireworks /v1/chat/completions
priority Responses -> 内部转换为 Chat Completions
```

如果转换复杂，可以第一版复用 LiteLLM 或只支持常见 Responses input -> Chat messages 的最小转换。

## 12. 推荐 MVP 决策

第一版优先：

```text
OpenAI-compatible /v1/responses proxy
OpenAI-compatible /v1/chat/completions proxy
model mapping
key pool
rendezvous sticky routing
failover/cooldown
prompt_cache_key + x-session-affinity
usage/cached_tokens 归一化
SQLite 配置
WebUI Dashboard
WebUI Key Pool
WebUI Model Mapping
request log without prompt
```

第一版暂缓：

```text
Anthropic /v1/messages
多用户系统
复杂权限
Redis
分布式状态
完整 billing
长期分析报表
自动从 Fireworks billing 拉成本
完整 Responses lifecycle 代理 list/get/delete
```

## 13. 推荐默认策略

```text
normal:          /v1/responses
fast:            /v1/responses + router model id
priority:        /v1/chat/completions + service_tier=priority
fast-priority:   /v1/chat/completions + router model id + service_tier=priority
```

key 选择：

```text
stable_key = prompt_cache_key || user || session headers || derived prefix hash
selected_key = rendezvous_hash(stable_key + upstream_model, healthy_keys)
```

failover：

```text
only on upstream/key/network failure
```

日志：

```text
metadata + usage + key fingerprint + stable_key hash
no full prompt
no full key
```

## 14. 关联文件

当前部署中的参考文件：

```text
/root/sub2api-deploy/fireworks-shim/app.py
/root/sub2api-deploy/fireworks2api_prompt.md
/root/sub2api-deploy/fireworks_test_results.md
/root/sub2api-deploy/fireworks_shim_conversion_tests.md
/root/sub2api-deploy/fireworks_responses_cache_repro.py
```

导出的 key 文件：

```text
/root/sub2api-deploy/fireworks_shim_keys.env
```

注意：

```text
fireworks_shim_keys.env 包含真实 Fireworks API key。
不要提交。
不要复制到不可信位置。
权限应保持 600。
```
