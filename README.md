# UNBOUND — chatbot, developer API, and on-demand model infrastructure

A working local product: sales landing page, account signup/login, chat playground,
customer API keys, token usage, credit balances, Stripe-hosted credit checkout, and
an OpenRouter/self-hosted model router. Default mode is **mock inference**: simulated
responses and token counters, with zero charges. No GPUs, payments, or external
model calls are configured by default.

## Run

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
cp .env.example .env  # only for a new installation; preserve an existing .env
.venv/bin/python scripts/dev.py
```

Open http://127.0.0.1:8080. Create an account to use chat, manage keys, and see usage.
The development launcher reads `.env` without evaluating it as shell commands.
No JavaScript build step is needed. API documentation: `/docs`.

Alternatively, `docker compose up --build` serves the same site and API with a
persistent named database volume. Back up the SQLite database and its WAL safely
using SQLite's backup API; do not copy a live database file alone. This initial
release supports **one gateway process/replica only**.

## Model routing

```text
Website / OpenAI-compatible coding client
                 ↓
Accounts, API keys, credit reservation, per-user request limits
                 ↓
              Model router
       ┌─────────┴──────────┐
  model=auto             model=glm
       ↓                      ↓
OpenRouter when configured   Self-hosted GLM
       ↓                      ↓
HTTP availability failure → Start GPU → wait for health → infer
                 ↓
       Provider token usage → settle wallet → request history
```

- Set `ARLIAI_API_KEY` to make `auto` use ArliAI's hosted
  `Qwen3.5-27B-Derestricted` route. This route is configured at zero customer
  cost for the chat-only MVP and never starts the GPU worker. The provider's
  free-plan concurrency and context limits still apply.
- Set `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` for everyday hosted chat. The
  configurable starting model ID is `deepseek/deepseek-v4.1-flash-1017`; no claim is made that
  it is the newest or fastest model. Choose any compatible model and set its retail
  rates before offering it. API keys remain server-side.
- `auto` uses ArliAI when configured, then OpenRouter, then the local worker.
  Successful hosted calls never wake the GPU.
- `glm-5.3` explicitly routes to OpenRouter's `z-ai/glm-5.3` model. It uses a
  separate configurable retail price snapshot and requires `OPENROUTER_API_KEY`.
  OpenRouter may select among upstream hosts for that model.
- `glm` (or `MODEL_NAME`) explicitly selects self-hosted inference. Messages on
  `auto` or `glm-5.3` are sent to OpenRouter and its selected provider; the UI
  discloses this.
- `ROUTER_FALLBACK=true` allows one self-hosted retry on HTTP 429, 500, 502, 503 or
  504 before output begins. It requires a real self-hosted worker. A real request
  is never silently replaced with mock output.
- Provider refusals, content-filter results, 400/401/402/403 errors and mid-stream
  failures do not trigger rerouting. Refusals remain visible. Connection failures
  return 502 rather than risking duplicate inference after an ambiguous timeout.
- JSON responses expose `routing.provider`, `routing.model` and `routing.fallback`.
  Streaming responses expose `X-Inference-Provider` and `X-Request-ID`.

## Developer API and coding agents

Create a customer key in the workspace (shown once). Only its SHA-256 hash is
stored. Revocation prevents future requests immediately; already-running requests
may finish. Keys share their account's wallet and rate limit.

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="ub_YOUR_KEY")
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Explain this code."}],
    max_tokens=1024,
)
print(response.choices[0].message.content)
```

Endpoints: `GET /v1/models`, `POST /v1/chat/completions`, authenticated `GET /worker`.
Chat Completions supports text messages, tool definitions, tool results, tool
choice, streaming, sampling parameters and supported reasoning options. Agent
clients execute their own tools; the gateway does not execute code. Responses
API, images, arbitrary vLLM overrides, multiple candidates (`n>1`), and unrestricted
context sizes are outside this version. A coding client must support Chat Completions.

For OpenRouter reasoning models, preserve the complete `reasoning_details` array
returned on an assistant message and send that assistant message back unchanged on
the next turn. The gateway forwards these nested details, and the browser chatbot
retains them without displaying them. This supports provider reasoning continuity;
it is not an interface for inventing or editing hidden reasoning.

Input admission uses a conservative UTF-8 request size budget with template
headroom; **it is not a billable tokenizer estimate**. Usage comes from the actual
provider's `prompt_tokens` and `completion_tokens`. Output limits are enforced and
passed upstream. Configure `MAX_INPUT_TOKENS + MAX_OUTPUT_TOKENS` within the worker's
context capacity. For this release the defaults sum to 16,384.

## Token accounting and credits

Money is integer **micro USD**: 1 USD = 1,000,000 micro USD. Environment rates are
micro USD per million tokens. Defaults are illustrative retail preview rates,
not a statement of upstream prices or profitable margins.

1. Before inference, SQLite atomically reserves credits for the maximum admitted
   input plus requested output tokens, using the selected route's price snapshot.
2. Concurrent requests cannot reserve the same balance. Insufficient available
   credit returns 402 before waking the GPU or calling OpenRouter.
3. Exact provider usage settles the reservation. The unused balance is released.
   Charge = ceil((input × input_rate + output × output_rate) / 1,000,000).
4. Settlement is idempotent. A successful billable request creates one debit in
   the append-only ledger. OpenRouter and GLM can have different retail rates.
5. Fallback releases the failed route's reservation and reserves the GLM rate.
   History records both route attempts with their provider and status.
6. Mock usage has synthetic token counters, `mock` status and a zero charge.

Streaming requests force `stream_options.include_usage=true`. The SSE parser
handles split network frames and collects provider-reported usage. A downstream
disconnect no longer cancels the upstream request: the gateway drains it within
`STREAM_TIMEOUT`, records the final usage, and settles the reservation. A bounded
queue disconnects slow consumers while accounting continues. `MAX_STREAMS` caps
connected and detached streams. Missing or invalid usage is never guessed: the
reservation is released and an audit status is recorded. Stream errors cannot
change an HTTP status already sent. A process crash can still interrupt final
accounting, so production reconciliation against provider usage records remains
a launch requirement.

`TRIAL_CREDIT_MICRO_USD` defaults to zero. Mock chat is still free. Paid inference
requires a funded balance. Stripe Checkout is disabled until all of
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and a valid `PUBLIC_ORIGIN` are set.
Live mode also requires HTTPS and `COOKIE_SECURE=true`.

The browser creates a server-defined credit-pack order and redirects to Stripe's
hosted page. Credits are granted only after a signed webhook is received and the
gateway retrieves the canonical Checkout Session from Stripe. It verifies the
stored user, amount, currency, mode, livemode, session ID, and paid status in one
atomic transaction. Duplicate and out-of-order webhooks cannot double-credit.
Configure webhook delivery for `checkout.session.completed`,
`checkout.session.async_payment_succeeded`, `checkout.session.expired`, and
`checkout.session.async_payment_failed` at `/api/billing/webhook`. Test-mode
payments create a separate test balance that cannot fund real inference.

An authenticated internal grant endpoint remains available for verified offline
payments or intentional promotional allocations:

```sh
curl http://127.0.0.1:8080/api/admin/credits \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H 'Idempotency-Key: your-unique-payment-reference' \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"USER_ID_FROM_API_ME","amount_micro_usd":5000000}'
```

Set `ADMIN_API_KEY` to a random secret of 32+ characters. Duplicate grant references
are safe to retry; reusing a reference with different details returns 409. Customers
cannot grant themselves credits. This endpoint is an internal administrative
operation and must only follow independent payment verification.

`GET /api/me`, `/api/usage`, `/api/keys`, `/api/ledger` are account-scoped. Usage
returns all-time totals, 30-day daily aggregates, and the most recent 50 attempts.
Optional `API_KEYS` are legacy **unmetered operator keys**; never give these to
customers. Customer keys are created through the authenticated account endpoint.

## Sessions and operation

Passwords use salted PBKDF2-HMAC-SHA256 with 600,000 iterations. Browser sessions
use random server-side tokens stored hashed, 7-day expiry, HttpOnly SameSite=Strict
cookies, and same-origin checks on cookie-authenticated routes. Authentication
and per-account inference limits are enforced in memory. Set `COOKIE_SECURE=true`
and `PUBLIC_ORIGIN=https://your-domain` behind production TLS. Do not trust arbitrary
forwarded headers from the internet. Prompts, passwords and API keys are not logged
or stored in the application database; chat history exists only in the browser
page session. Upstream providers have their own retention policies.

Before public paid access: configure and test real Stripe webhooks, add refund and
dispute handling, email verification and password recovery, reconcile provider
usage after process crashes, add independent GPU cost limits, edge abuse controls,
backup/restore testing, deployment monitoring, and real-model load tests. Preview
rates and UI should not be presented as finalized commercial terms. This is a
tested local MVP, not a production launch certification.

## Connect Google Compute Engine later

1. Choose a GLM checkpoint, a pinned/tested vLLM image, GPU count, context length,
   and tool/reasoning parsers from that model's official recipe. Large GLM models
   may require several expensive GPUs; no checkpoint or GPU is provisioned here.
2. Create a Linux GPU VM with drivers, Docker and NVIDIA Container Toolkit. Keep
   the model cache and Docker configuration on persistent disk; enable Docker at boot.
3. Copy `infra/worker-compose.yaml` to the VM and supply its required environment
   values. Run `docker compose -f worker-compose.yaml up -d`. Verify real inference,
   tool calls, token usage and health, then stop the VM.
4. Give the worker a stable private IP and allow port 8000 only from the gateway.
   Set `PROVIDER=gcp`, `GCP_PROJECT`, `GCP_ZONE`, `GCP_INSTANCE`, `UPSTREAM_URL`,
   and matching `UPSTREAM_API_KEY`. The controller only starts/stops an existing VM.
5. Use application default credentials or an attached identity with
   `compute.instances.get`, `compute.instances.start`, `compute.instances.stop`,
   and `compute.zoneOperations.get`, scoped to the dedicated resources.
6. Validate startup, concurrent usage, failure recovery, and idle stop in staging.
   Add an independent maximum-runtime shutdown watchdog and billing alerts. A
   crashed gateway cannot stop a VM; this implementation is not a spending cap.

The CPU gateway stays running. Requests share worker startup and hold leases for
the full response. The worker stops after `IDLE_SECONDS` without active requests.
Cold VM/model startup can take minutes; clients and proxies must tolerate
`STARTUP_TIMEOUT` plus generation time. Model caching avoids repeat downloads but
not model loading. Scale the gateway only after moving lifecycle coordination
and reservations to a shared production control plane.

Google's non-billable free trial does not permit GPUs or quota increases. Upgrading
retains eligible unexpired credits, but quota, availability and credit coverage
must be checked. Stopped compute and retained disks have different billing rules.

Sources:
- https://cloud.google.com/free/docs/free-cloud-features
- https://cloud.google.com/compute/docs/instances/stop-start-instance
- https://docs.vllm.ai/en/stable/features/tool_calling/
- https://github.com/zai-org/GLM-4.5
- https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request
- https://openrouter.ai/deepseek/deepseek-v4.1-flash-1017

## Verification

```sh
.venv/bin/python -m pytest -q
node --check frontend/app.js
docker compose config --quiet
```

Tests cover account isolation, session auth, key revocation, body/parameter limits,
credit grants, atomic reservations, idempotent settlement, precise token billing,
client-disconnected streams, Stripe signature/canonical-session verification,
duplicate payment events, routing, recovery, and GPU lifecycle. External providers
are simulated in tests. Live Stripe, OpenRouter, and GPU checks need credentials.
