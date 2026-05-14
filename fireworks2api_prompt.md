# fireworks2api 第一版实现任务单

> Historical planning note. Current implementation has moved beyond parts of this
> MVP task list; prefer `README.md`, `AGENTS.md`, and `docs/` for current behavior.

## 0. 项目定位

新建独立项目：`fireworks2api`。

目标是实现一个轻量级 Fireworks API 代理/兼容层，让 OpenAI-compatible 客户端可以更稳定地使用 Fireworks，并重点优化：

- 多 Fireworks API key 管理；
- prompt/KV cache 命中率；
- sticky routing；
- failover / cooldown；
- fast router / priority tier 路由；
- 基础 WebUI 管理体验；
- Docker 部署。

第一版目标不是做完整平台，而是做一个**可运行、可测试、可部署、边界清晰的 MVP**。

---

## 1. grok2api 参考策略

可以参考项目：

- https://github.com/chenyme/grok2api

参考范围仅限：

1. WebUI 页面组织方式；
2. 管理后台使用体验；
3. Docker / docker-compose / `.env.example` 组织方式；
4. README 使用说明风格；
5. 简单部署体验。

不要参考或照搬：

1. grok2api 后端业务逻辑；
2. 上游 API 路由逻辑；
3. key 选择策略；
4. 请求转换逻辑；
5. cache / sticky routing / failover 实现。

fireworks2api 的后端必须围绕 Fireworks 重新设计。

如果时间有限，第一版可以先不深度研究 grok2api，只把它作为 WebUI 和部署体验参考；后端实现应优先参考 Fireworks 官方文档和实际 API 测试。

---

## 2. 官方文档与待验证事实

优先参考：

- https://docs.fireworks.ai/llms.txt
- Fireworks Responses API docs
- Fireworks Chat Completions API docs
- Fireworks Serverless Priority / Turbo / Fast router docs
- Fireworks rate limit / quota / error code docs
- Fireworks model / quota / billing / API key docs

已知事实：

1. Fireworks `/v1/responses` 已观察到支持 prompt cache 统计。
2. Fireworks `/v1/chat/completions` 支持 prompt cache 统计。
3. 不同 Fireworks API key 之间 prompt cache 不共享。
4. 因此多 key pool 不能随机 round-robin，必须 sticky routing。
5. Turbo / fast 类型通常是换 model/router id，不是 `service_tier`。
6. `service_tier: "priority"` 目前按官方文档只视为 Chat Completions 明确支持。

第一版实现时必须兼容解析 cached tokens：

- `usage.prompt_tokens_details.cached_tokens`
- `usage.input_tokens_details.cached_tokens`

字段缺失时按 `0` 处理，并保留 `raw_usage` 便于排查。

待实际 API 测试验证：

1. Responses API 是否接受 `prompt_cache_key`；
2. Responses API 是否接受 `prompt_cache_isolation_key`；
3. Responses API 是否接受 `user`；
4. Responses API 是否接受 `x-session-affinity`；
5. Responses API 是否支持 `service_tier=priority`；
6. fast router id 与 `service_tier=priority` 组合是否稳定；
7. Fireworks 429 / 402 / 412 / 403 等错误的实际响应格式。

在验证前，不得把待验证能力写死为强依赖。

---

## 3. 第一版 MVP 范围

### 3.1 必须实现

#### API 代理

- `GET /health`
- `POST /v1/chat/completions`
- `POST /v1/responses`

仅承诺 `/v1/*` public routes；不维护 `/responses`、`/models`、`/messages`、`/rerank` 等未版本化别名。

#### Fireworks 上游

默认 upstream base URL：

```text
https://api.fireworks.ai/inference/v1
```

#### Key pool

- 支持从环境变量初始化多个 Fireworks key；
- 支持 SQLite 持久化 key 配置；
- 支持 enable / disable / cooldown；
- 支持 key fingerprint；
- 不允许任何接口、日志、WebUI 返回完整 key。

#### Sticky routing

- 使用 rendezvous hash 或 consistent hash；
- 同一个 stable key + model 应尽量固定落到同一个 Fireworks key；
- 只有 key 不健康、限流、额度异常、上游 5xx 或网络错误时才 failover。

#### Failover / cooldown

- 支持 429 / 5xx / network timeout 的短 cooldown；
- 支持 quota / billing / suspended 类错误的长 cooldown 或 disable；
- 单请求最多尝试 2-3 个 key；
- streaming 已经开始输出后不再 failover。

#### Model mapping

- 支持 alias 到 Fireworks model/router id 的映射；
- 支持 normal / fast / priority / fast-priority；
- 精确 alias 优先，suffix rule 次之。

#### Usage / cache stats

- 提取 input tokens；
- 提取 output tokens；
- 提取 cached tokens；
- 计算 cache hit ratio；
- 记录最近请求日志，但不保存完整 prompt/body。

#### 管理接口

- `GET /admin/overview`
- `GET /admin/keys`
- `POST /admin/keys`
- `PATCH /admin/keys/{name}`
- `DELETE /admin/keys/{name}`
- `POST /admin/keys/{name}/enable`
- `POST /admin/keys/{name}/disable`
- `POST /admin/keys/{name}/clear-cooldown`
- `GET /admin/models`
- `POST /admin/models`
- `PATCH /admin/models/{alias}`
- `DELETE /admin/models/{alias}`
- `GET /admin/requests`

#### WebUI

第一版只做极简 WebUI：

1. Dashboard；
2. Key Pool；
3. Model Mapping；
4. Recent Requests。

WebUI 只覆盖 MVP 管理功能，不做复杂分析平台。

#### 部署

- Dockerfile；
- docker-compose.yml；
- `.env.example`；
- README；
- `/app/data` 数据目录挂载。

---

### 3.2 第一版明确不做

- Anthropic `/v1/messages`；
- 多用户系统；
- 完整权限系统；
- billing 系统；
- Redis；
- 分布式部署；
- 长期历史分析；
- 完整 Cache Analysis 页面；
- Routing Strategy 配置页面；
- Settings 全量编辑页面；
- 完整 OpenAI Responses API emulation；
- 完整 Responses -> Chat Completions 无损转换。

---

## 4. API 兼容边界

### 4.1 `/v1/chat/completions`

第一版作为 Fireworks Chat Completions passthrough proxy。

要求：

- 保留 OpenAI-compatible chat completions 基础字段；
- 透传 Fireworks 已知支持扩展字段；
- 支持 streaming 原样转发；
- 支持 `service_tier="priority"`；
- 支持 model alias 替换；
- 注入或透传 cache/sticky 相关字段。

支持字段至少包括：

- `model`
- `messages`
- `stream`
- `temperature`
- `top_p`
- `max_tokens`
- `max_completion_tokens`
- `stop`
- `tools`
- `tool_choice`
- `response_format`
- `user`
- `service_tier`
- `prompt_cache_key`
- `prompt_cache_isolation_key`
- `reasoning_effort`
- `thinking`
- `reasoning_history`
- `context_length_exceeded_behavior`
- `top_k`
- `min_p`
- `typical_p`
- `repetition_penalty`
- `seed`
- `logprobs`
- `top_logprobs`
- `raw_output`
- `return_token_ids`
- `perf_metrics_in_response`

注意：

- `thinking` 和 `reasoning_effort` 不要同时发送给 Fireworks；
- 如果客户端显式传 Fireworks 原生参数，优先使用客户端显式值；
- 不要自行构造 Fireworks 不支持的字段。

---

### 4.2 `/v1/responses`

第一版作为 Fireworks Responses passthrough proxy，不承诺完整 OpenAI Responses emulation。

要求：

- 默认上游走 Fireworks `/v1/responses`；
- 支持 model alias 替换；
- 支持 streaming 原样转发；
- 尽量保留 Fireworks Responses 原始响应结构；
- 解析 usage/cached_tokens；
- 不做完整 response lifecycle 本地实现。

第一版不实现：

- 本地 `store`；
- 本地 `previous_response_id` 状态管理；
- list/get/delete response；
- OpenAI Responses 与 Fireworks Responses 的完整规范化转换。

如果 `/v1/responses` 请求需要 priority：

- 当前实现对简单非流式文本请求默认走跨接口 fallback：
  `/v1/responses` + `service_tier=priority` -> Fireworks Chat Completions priority；
- 返回仍合成为 Responses-shaped payload，id 形如 `resp_fallback_chatcmpl-*`；
- 不建立 Responses lifecycle 绑定；
- stream / tools / MCP / image / reasoning / previous_response_id / lifecycle 等复杂 priority 请求仍返回 `400`。

---

## 5. 路由矩阵

| Client endpoint | Model mode | Request tier | Upstream endpoint | Upstream model | Extra params |
|---|---|---|---|---|---|
| `/v1/responses` | normal | none | `/v1/responses` | model id | none |
| `/v1/responses` | fast | none | `/v1/responses` | router id | none |
| `/v1/responses` | priority | priority | `/v1/chat/completions` fallback for simple non-stream text | model id | synthesize Responses-shaped response; complex priority returns 400 |
| `/v1/responses` | fast-priority | priority | `/v1/chat/completions` fallback for simple non-stream text | router id | synthesize Responses-shaped response; complex priority returns 400 |
| `/v1/chat/completions` | normal | none | `/v1/chat/completions` | model id | none |
| `/v1/chat/completions` | fast | none | `/v1/chat/completions` | router id | none |
| `/v1/chat/completions` | priority | priority | `/v1/chat/completions` | model id | `service_tier=priority` |
| `/v1/chat/completions` | fast-priority | priority | `/v1/chat/completions` | router id | `service_tier=priority` |

如果客户端直接传 `service_tier="priority"`：

- Chat Completions：允许；
- Responses：MVP 返回 400。

---

## 6. Model mapping 设计

默认模型映射示例：

```text
kimi-k2.6 -> accounts/fireworks/models/kimi-k2p6
kimi-k2.6-turbo -> accounts/fireworks/routers/kimi-k2p6-turbo
glm-5.1 -> accounts/fireworks/models/glm-5p1
glm-5.1-fast -> accounts/fireworks/routers/glm-5p1-fast
deepseek-v4-pro -> accounts/fireworks/models/deepseek-v4-pro
MiniMax-M2.7 -> accounts/fireworks/models/minimax-m2p7
```

每条 model mapping 字段：

- `alias`
- `upstream_model`
- `mode`: `normal` / `fast` / `priority` / `fast_priority`
- `upstream_endpoint`: `responses` / `chat_completions` / `auto`
- `supports_priority`
- `supports_responses`
- `supports_chat_completions`
- `enabled`

解析优先级：

1. 精确 alias 匹配优先；
2. suffix rule 次之；
3. unknown model 默认返回 400；
4. 是否允许 unknown model 透传由设置项控制，默认关闭。

suffix rule：

- `xxx-fast`
- `xxx-priority`
- `xxx-fast-priority`
- `xxx-turbo-priority`

suffix rule 只有在 base alias 存在时才生效。

---

## 7. Key pool 设计

### 7.1 初始化方式

支持：

```text
FIREWORKS_API_KEYS=key1,key2,key3
```

也支持：

```json
FIREWORKS_API_KEYS_JSON=[{"name":"fw1","key":"..."},{"name":"fw2","key":"..."}]
```

初始化规则：

- 如果 SQLite 中没有 key，则从 env 导入；
- 如果 SQLite 中已有 key，默认以 SQLite 为准；
- 可通过配置允许每次启动同步 env key；
- env key 自动生成 name，例如 `fw-1`、`fw-2`。

### 7.2 key 字段

每个 key 记录：

- `name`
- `encrypted_key` 或 `api_key_ciphertext`
- `fingerprint`
- `enabled`
- `cooldown_until`
- `disabled_reason`
- `last_error_type`
- `last_error_at`
- `created_at`
- `updated_at`

第一版可以明文存储 key，但必须：

- API 不返回完整 key；
- WebUI 不显示完整 key；
- 日志不打印完整 key；
- README 明确 DB 文件权限风险；
- schema 预留加密字段；
- 后续支持 `SECRET_KEY` 加密。

fingerprint：

```text
sha256(api_key).hexdigest()[:12]
```

日志只允许记录 name 或 fingerprint。

---

## 8. Sticky routing 设计

### 8.1 stable_key 来源优先级

MVP 只使用显式 affinity 来源：

1. request body `prompt_cache_key`
2. request body `user`
3. header `x-session-affinity`
4. header `x-multi-turn-session-id`
5. header `session_id`
6. header `conversation_id`
7. fallback: `model + proxy client identity`

MVP 不从完整 prompt 或最后一条用户消息派生 stable_key。

原因：

- 容易降低 cache 命中；
- 容易产生隐私泄露风险；
- canonicalization 复杂；
- 第一版不需要这部分复杂度。

### 8.2 hash 输入

实际路由 hash 输入：

```text
route_key = model_alias + ":" + stable_key
```

如果有 proxy client identity：

```text
route_key = client_identity + ":" + model_alias + ":" + stable_key
```

### 8.3 日志中的 stable key

日志不记录原始 stable_key。

只记录：

```text
stable_key_hash = hmac_sha256(LOG_HASH_SECRET, stable_key)[:12]
```

如果没有 `LOG_HASH_SECRET`，使用随机启动密钥，重启后 hash 不保证一致。

### 8.4 上游 cache 参数注入

Chat Completions：

- 如果客户端已有 `prompt_cache_key`，保留；
- 如果没有，则可设置为 stable_key hash 或 stable_key 的安全派生值；
- 如果客户端已有 `user`，保留；
- 可设置 header `x-session-affinity`。

Responses：

- 是否传 `prompt_cache_key` / `user` / `x-session-affinity` 需要配置开关；
- 默认只传官方明确支持或实测确认的字段；
- 避免因未知字段导致上游 400。

---

## 9. Failover / cooldown 设计

### 9.1 选择 key

流程：

1. 根据 route_key 用 rendezvous hash 对 healthy keys 排序；
2. 选择排名最高的 key；
3. 如果失败且错误可 failover，则尝试下一个 healthy key；
4. 单请求最多尝试 `MAX_UPSTREAM_ATTEMPTS`，默认 3；
5. cooldown 结束后 key 自动重新进入 healthy 集合。

### 9.2 错误分类

| HTTP/status/body | error_type | failover | cooldown | disable |
|---|---|---:|---:|---:|
| 400 | bad_request | no | no | no |
| 401 | auth_error | yes | long | maybe |
| 402 | billing_or_quota | yes | long | maybe |
| 403 | permission_or_account | yes | long | maybe |
| 404 | model_not_found | no | no | no |
| 408 / timeout | timeout | yes | short | no |
| 409 / concurrency-like | concurrency_limit | yes | short | no |
| 412 | precondition_or_suspended | yes | long | maybe |
| 429 | rate_limit_or_capacity | yes | short | no |
| 5xx | upstream_5xx | yes | short | no |
| network error | network_error | yes | short | no |

默认 cooldown：

```text
429 / concurrency: 60s
5xx: 20s
timeout / network: 30s
billing / quota / suspended: 1h or disabled
auth / permission: disabled unless configured otherwise
```

### 9.3 streaming 规则

- 在上游响应 headers 返回前，可以 failover；
- 一旦开始向客户端输出 streaming chunk，不再 failover；
- streaming 中断时记录错误，但不尝试换 key 重放请求。

### 9.4 所有 key 不可用

返回：

```text
503 Service Unavailable
```

响应体不包含完整 key、完整 prompt、完整 Authorization。

---

## 10. SQLite schema

使用 SQLite 作为第一版配置存储。

启动时启用：

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
```

### 10.1 schema_migrations

```sql
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
```

### 10.2 keys

```sql
CREATE TABLE keys (
  name TEXT PRIMARY KEY,
  api_key_ciphertext TEXT NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1,
  cooldown_until TEXT,
  disabled_reason TEXT,
  last_error_type TEXT,
  last_error_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### 10.3 model_mappings

```sql
CREATE TABLE model_mappings (
  alias TEXT PRIMARY KEY,
  upstream_model TEXT NOT NULL,
  mode TEXT NOT NULL,
  upstream_endpoint TEXT NOT NULL DEFAULT 'auto',
  supports_priority INTEGER NOT NULL DEFAULT 0,
  supports_responses INTEGER NOT NULL DEFAULT 1,
  supports_chat_completions INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### 10.4 settings

```sql
CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### 10.5 request_logs

```sql
CREATE TABLE request_logs (
  id TEXT PRIMARY KEY,
  timestamp TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  model_alias TEXT,
  upstream_model TEXT,
  key_fingerprint TEXT,
  stable_key_hash TEXT,
  stream INTEGER NOT NULL DEFAULT 0,
  service_tier TEXT,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cached_tokens INTEGER DEFAULT 0,
  cache_hit_ratio REAL DEFAULT 0,
  latency_ms INTEGER,
  status_code INTEGER,
  error_type TEXT,
  upstream_request_id TEXT
);

CREATE INDEX idx_request_logs_timestamp ON request_logs(timestamp);
CREATE INDEX idx_request_logs_model ON request_logs(model_alias);
CREATE INDEX idx_request_logs_key ON request_logs(key_fingerprint);
```

request logs 保留最近 N 条，默认 1000。

### 10.6 runtime stats

MVP 可以内存聚合 runtime stats，定期或按需写入 SQLite。

不要在每个 streaming chunk 写 SQLite。

---

## 11. 管理接口设计

所有 `/admin/*` 必须校验 `ADMIN_TOKEN`。

没有 `ADMIN_TOKEN` 时：

- admin 写操作禁用；
- WebUI 显示只读或不可用状态。

### 11.1 overview

`GET /admin/overview` 返回：

- service status；
- upstream base url；
- key total；
- healthy key count；
- cooldown key count；
- disabled key count；
- recent request count；
- recent error count；
- total input tokens；
- total output tokens；
- total cached tokens；
- cache hit ratio；
- average latency。

### 11.2 keys

key API 不得返回完整 key。

返回字段：

- name；
- fingerprint；
- enabled；
- cooldown_until；
- disabled_reason；
- last_error_type；
- last_error_at；
- recent success/failure；
- recent input/output/cached tokens；
- cache hit ratio。

### 11.3 models

支持增删改查 model mapping。

### 11.4 requests

`GET /admin/requests` 返回最近 N 条请求日志。

支持基础筛选：

- model；
- key fingerprint；
- error_type；
- status_code。

---

## 12. WebUI 第一版页面

前端技术选型建议：

- 第一版优先 FastAPI static files + vanilla JS 或轻量 React/Vite；
- 如果使用 React/Vite，Docker 构建必须保持简单；
- UI 风格可以参考 grok2api 管理面板，但不要复制后端逻辑。

页面：

### 12.1 Dashboard

展示：

- 服务状态；
- upstream base url；
- key pool 状态；
- 最近请求数；
- 错误数；
- token 统计；
- cache hit ratio；
- 平均延迟。

### 12.2 Key Pool

展示：

- name；
- fingerprint；
- enabled；
- cooldown_until；
- disabled_reason；
- last_error_type；
- token/cache 简要统计。

操作：

- add key；
- delete key；
- enable；
- disable；
- clear cooldown。

禁止显示完整 key。

### 12.3 Model Mapping

展示和编辑：

- alias；
- upstream_model；
- mode；
- upstream_endpoint；
- supports_priority；
- enabled。

### 12.4 Recent Requests

展示：

- timestamp；
- endpoint；
- model alias；
- upstream model；
- selected key fingerprint；
- stable key hash；
- stream；
- service tier；
- input/output/cached tokens；
- cache hit ratio；
- latency；
- status code；
- error type。

不展示完整 prompt/body/header。

---

## 13. 安全要求

### 13.1 Token 分离

必须支持：

- `ADMIN_TOKEN`：用于 `/admin/*` 和 WebUI 管理；
- `PROXY_API_KEYS`：用于 `/v1/*` 代理接口。

`ADMIN_TOKEN` 不应默认等同于 proxy key。

`PROXY_API_KEYS` 示例：

```text
PROXY_API_KEYS=sk-local-1,sk-local-2
```

OpenAI-compatible 客户端使用：

```text
Authorization: Bearer sk-local-1
```

### 13.2 脱敏

禁止在以下位置出现完整 Fireworks key：

- logs；
- API response；
- WebUI；
- exception traceback；
- request log；
- error body。

禁止默认记录完整：

- prompt；
- messages；
- input；
- Authorization header；
- upstream request body。

### 13.3 CORS / CSRF

- CORS 默认关闭或只允许同源；
- 如开启 CORS，必须通过配置显式允许 origin；
- WebUI 管理操作必须带 admin token；
- 不允许无认证跨站调用管理接口。

### 13.4 upstream base url

MVP 中 upstream base url 通过环境变量或配置文件设置。

WebUI 不提供任意修改 upstream base url 的能力，避免 SSRF 风险。

### 13.5 Debug 模式

Debug logging 默认关闭。

即使开启 debug，也不得记录完整 Fireworks key 或 Authorization。

记录完整 prompt/body 必须单独显式开启，并在 README 中标明风险。

---

## 14. 项目结构

推荐结构：

```text
fireworks2api/
  app/
    __init__.py
    main.py
    config.py
    security.py
    logging_utils.py

    api/
      __init__.py
      health.py
      chat_completions.py
      responses.py
      admin.py

    core/
      __init__.py
      model_resolver.py
      key_pool.py
      sticky_router.py
      failover.py
      error_classifier.py
      usage.py
      redaction.py

    upstream/
      __init__.py
      fireworks_client.py
      stream_proxy.py

    storage/
      __init__.py
      db.py
      migrations.py
      repositories.py
      schemas.sql

    webui/
      index.html
      app.js
      style.css

  tests/
    unit/
      test_model_resolver.py
      test_sticky_router.py
      test_key_pool.py
      test_error_classifier.py
      test_usage.py
      test_redaction.py
    integration/
      test_chat_proxy_mock.py
      test_responses_proxy_mock.py
      test_failover_mock.py
      test_streaming_mock.py
    live/
      test_fireworks_cache_live.py

  scripts/
    live_cache_test.py
    init_db.py

  data/
    .gitkeep

  Dockerfile
  docker-compose.yml
  .env.example
  README.md
  pyproject.toml
```

---

## 15. 测试计划

### 15.1 默认 CI 测试

默认不需要真实 Fireworks key。

必须覆盖：

1. model alias 解析；
2. suffix rule；
3. rendezvous hash 稳定性；
4. stable_key 来源优先级；
5. failover key 顺序；
6. cooldown 进入和恢复；
7. error classifier；
8. usage/cached_tokens 解析；
9. key redaction；
10. chat proxy mocked upstream；
11. responses proxy mocked upstream；
12. streaming passthrough；
13. streaming started 后不 failover；
14. 400 不 failover；
15. 429 / 5xx failover。

### 15.2 live Fireworks 测试

必须显式启用：

```text
RUN_LIVE_FIREWORKS_TESTS=1
FIREWORKS_API_KEYS=...
```

测试：

1. 单 key cache 测试；
2. 多 key cache 不共享测试；
3. sticky routing 测试；
4. priority chat completions 测试；
5. fast router 测试；
6. fast-priority 测试；
7. Responses cached_tokens 字段验证；
8. Responses cache 请求参数验证；
9. 错误码/限流头验证。

live 测试不得输出完整 key。

---

## 16. 实施步骤

按以下顺序实现：

### Phase 1: 项目骨架

1. 创建 FastAPI 项目；
2. 配置 `pyproject.toml`；
3. 创建 app 目录结构；
4. 实现配置加载；
5. 实现基础日志与脱敏工具；
6. 实现 `/health`。

### Phase 2: SQLite 与配置模型

1. 实现 SQLite 初始化；
2. 实现 migrations；
3. 创建 keys / model_mappings / settings / request_logs 表；
4. 从 env 导入 Fireworks keys；
5. 初始化默认 model mappings。

### Phase 3: 核心路由组件

1. 实现 model resolver；
2. 实现 key pool；
3. 实现 stable_key 提取；
4. 实现 rendezvous hash sticky router；
5. 实现 error classifier；
6. 实现 cooldown/failover。

### Phase 4: 上游代理

1. 实现 Fireworks httpx async client；
2. 实现 `/v1/chat/completions` non-stream proxy；
3. 实现 `/v1/chat/completions` stream proxy；
4. 实现 `/v1/responses` non-stream proxy；
5. 实现 `/v1/responses` stream proxy；
6. 实现 `/responses` alias。

### Phase 5: usage 与 request log

1. 统一 usage 解析；
2. 提取 cached_tokens；
3. 计算 cache hit ratio；
4. 写入 request_logs；
5. 实现 request log retention。

### Phase 6: Admin API

1. 实现 admin token 校验；
2. 实现 overview；
3. 实现 keys 管理；
4. 实现 models 管理；
5. 实现 recent requests 查询。

### Phase 7: WebUI

1. 实现 Dashboard；
2. 实现 Key Pool 页面；
3. 实现 Model Mapping 页面；
4. 实现 Recent Requests 页面；
5. 接入 admin token；
6. 确认不展示完整 key/prompt/body。

### Phase 8: Docker 与文档

1. 编写 Dockerfile；
2. 编写 docker-compose.yml；
3. 编写 `.env.example`；
4. 编写 README；
5. 写明安全风险和部署建议。

### Phase 9: 测试与验证

1. 编写 unit tests；
2. 编写 mocked upstream integration tests；
3. 编写 streaming tests；
4. 编写 redaction tests；
5. 编写 live Fireworks 测试脚本；
6. 运行测试；
7. 修复问题。

---

## 17. 完成标准

第一版完成时应满足：

1. OpenAI-compatible chat client 可以通过 `/v1/chat/completions` 调用 Fireworks；
2. `/v1/responses` 可以代理 Fireworks Responses；
3. 多 key sticky routing 稳定；
4. key failover/cooldown 可工作；
5. priority chat completions 可工作；
6. fast router alias 可工作；
7. request logs 不泄露 key/prompt/Auth；
8. cached_tokens 可被解析和展示；
9. WebUI 可管理 keys 和 model mappings；
10. Docker 可一键启动；
11. 默认测试不需要真实 Fireworks key；
12. live 测试可显式启用。

---

## 18. 后续版本路线图

### v1.1

- Responses cache 参数实测后默认启用；
- documented subset 的 Responses -> Chat 转换；
- Key test / health check；
- Routing Strategy 页面；
- Cache Analysis 页面；
- Settings 页面；
- SECRET_KEY 加密 Fireworks key。

### v1.2

- Anthropic `/v1/messages`；
- 更完整 OpenAI Responses compatibility；
- 长期 metrics；
- Redis 可选；
- 多实例部署支持；
- 更细粒度 client API key 管理。
