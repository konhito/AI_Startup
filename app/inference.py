import asyncio
import json
import logging
import os
import time
import uuid

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from .routing import arliai_enabled, can_fallback, endpoint, openrouter_enabled, select_route, self_hosted
from .streaming import MeteredStream

# Keep provider-specific execution/templating overrides out of a billed public API.
ALLOWED = {'model', 'messages', 'stream', 'stream_options', 'max_tokens', 'max_completion_tokens',
    'temperature', 'top_p', 'frequency_penalty', 'presence_penalty', 'stop', 'seed', 'tools',
    'tool_choice', 'parallel_tool_calls', 'response_format', 'reasoning_effort', 'reasoning', 'n', 'user'}


def validate(body):
    if not isinstance(body, dict) or not isinstance(body.get('messages'), list) or not body['messages']:
        raise HTTPException(400, 'messages must be a nonempty list')
    if body.get('model') not in ('auto', 'qwen-derestricted', 'glm-5.3', os.getenv('MODEL_NAME', 'glm')):
        raise HTTPException(404, 'Unknown model')
    if set(body) - ALLOWED:
        raise HTTPException(400, 'Unsupported request fields: ' + ', '.join(sorted(set(body) - ALLOWED)))
    if 'stream' in body and not isinstance(body['stream'], bool):
        raise HTTPException(400, 'stream must be a boolean')
    if body.get('n', 1) != 1:
        raise HTTPException(400, 'Only n=1 is supported')
    if 'max_tokens' in body and 'max_completion_tokens' in body:
        raise HTTPException(400, 'Set only one output token limit')
    maximum = body.get('max_completion_tokens', body.get('max_tokens', 1024))
    if type(maximum) is not int or not 1 <= maximum <= int(os.getenv('MAX_OUTPUT_TOKENS', '4096')):
        raise HTTPException(400, 'Output token limit is outside the configured range')
    if 'stream_options' in body and not isinstance(body['stream_options'], dict):
        raise HTTPException(400, 'stream_options must be an object')
    if len(body['messages']) > 256:
        raise HTTPException(400, 'Too many messages')
    for message in body['messages']:
        if not isinstance(message, dict) or message.get('role') not in ('system', 'developer', 'user', 'assistant', 'tool', 'function'):
            raise HTTPException(400, 'Each message must have a supported role')
        content = message.get('content')
        if content is not None and not isinstance(content, str):
            raise HTTPException(400, 'This initial gateway supports text messages only')
    # This is an admission size bound, not a billable token estimate. Exact usage
    # comes from the provider. Leaves ample room for chat-template overhead.
    prompt_bytes = len(json.dumps({k: v for k, v in body.items() if k in ('messages', 'tools', 'tool_choice')},
                                 ensure_ascii=False).encode())
    if prompt_bytes + 2048 > int(os.getenv('MAX_INPUT_TOKENS', '12288')):
        raise HTTPException(400, 'Prompt exceeds the configured text input budget')
    return maximum


def mock_completion(body):
    content = ('Mock worker response. Your message reached the gateway successfully. '
               'Connect OpenRouter or a GPU worker for real model inference.')
    # Synthetic demo counters only. Never billed or represented as GLM tokenizer output.
    prompt_tokens = max(1, len(json.dumps(body['messages']).encode()) // 4)
    output_tokens = min(body.get('max_tokens', body.get('max_completion_tokens', 1024)), max(1, len(content) // 4))
    return {'id': 'chatcmpl-' + uuid.uuid4().hex, 'object': 'chat.completion', 'created': int(time.time()),
        'model': body['model'], 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content},
        'finish_reason': 'stop'}], 'usage': {'prompt_tokens': prompt_tokens,
        'completion_tokens': output_tokens, 'total_tokens': prompt_tokens + output_tokens}}


async def run_chat(request: Request, body, identity=None):
    maximum = validate(body)
    state = request.app.state
    if body['model'] == 'glm-5.3' and not openrouter_enabled():
        raise HTTPException(503, 'GLM-5.3 through OpenRouter is not configured')
    if body['model'] == 'qwen-derestricted' and not arliai_enabled():
        raise HTTPException(503, 'ArliAI Qwen is not configured')
    if identity:
        state.limiter.check(('inference', identity['user_id']), int(os.getenv('REQUESTS_PER_MINUTE', '60')))
    route = select_route(body['model'], state.mock)
    if body.get('stream') and len(state.stream_tasks) >= int(os.getenv('MAX_STREAMS', '32')):
        raise HTTPException(429, 'Stream capacity reached', headers={'Retry-After': '10'})
    fell_back = False
    # At most one fallback, only before any output has been returned to the caller.
    for attempt in range(2):
        usage_id = None
        acquired = False
        upstream = None
        cleaned = False
        provider_usage = None
        terminal = 'failed'
        request_body = dict(body)
        request_body['model'] = route.model
        if route.provider == 'arliai' and 'reasoning' not in request_body and 'reasoning_effort' not in request_body:
            request_body['reasoning_effort'] = os.getenv('ARLIAI_REASONING_EFFORT', 'none')
        if 'max_tokens' not in request_body and 'max_completion_tokens' not in request_body:
            request_body['max_tokens'] = maximum
        if request_body.get('stream'):
            request_body['stream_options'] = {**request_body.get('stream_options', {}), 'include_usage': True, 'continuous_usage_stats': False}
        if identity:
            usage_id = state.store.reserve(identity['user_id'], identity.get('id'), route.model,
                int(os.getenv('MAX_INPUT_TOKENS', '12288')), maximum, route.input_rate, route.output_rate, route.mock,
                provider=route.provider)
        routing = {'provider': route.provider, 'model': route.model, 'fallback': fell_back}

        async def cleanup():
            nonlocal cleaned
            if cleaned:
                return
            cleaned = True
            # Synchronous settlement happens before cancellable network cleanup.
            try:
                if usage_id:
                    state.store.settle(usage_id, provider_usage, terminal)
            finally:
                if acquired:
                    state.worker.release()
                if upstream is not None:
                    await upstream.aclose()

        try:
            if route.provider in ('mock', 'self-hosted'):
                try:
                    await state.worker.acquire()
                    acquired = True
                except OverflowError:
                    raise HTTPException(429, 'Worker capacity reached', headers={'Retry-After': '10'})
                except Exception:
                    logging.exception('Worker startup failed')
                    raise HTTPException(503, 'Worker unavailable; retry shortly', headers={'Retry-After': '30'})
            if route.mock:
                data = mock_completion(request_body)
                provider_usage = data['usage']
                terminal = 'mock'
                if not body.get('stream'):
                    await cleanup()
                    return JSONResponse({**data, 'routing': routing, 'metering': {
                        'request_id': usage_id, 'mock': True, 'cost_micro_usd': 0}})

                async def mock_events():
                    try:
                        base = {k: data[k] for k in ('id', 'created', 'model')}
                        base['object'] = 'chat.completion.chunk'
                        for delta, finish in [({'role': 'assistant', 'content': data['choices'][0]['message']['content']}, None), ({}, 'stop')]:
                            yield 'data: ' + json.dumps({**base, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}) + '\n\n'
                        yield 'data: ' + json.dumps({**base, 'choices': [], 'usage': provider_usage}) + '\n\n'
                        yield 'data: [DONE]\n\n'
                    finally:
                        await cleanup()
                return StreamingResponse(mock_events(), media_type='text/event-stream',
                    headers={'X-Inference-Provider': route.provider}, background=BackgroundTask(cleanup))
            url, headers = endpoint(route)
            upstream = await state.client.send(state.client.build_request('POST', url, json=request_body, headers=headers), stream=True)
            if body['model'] == 'auto' and can_fallback(route, upstream.status_code, state.mock) and attempt == 0:
                terminal = 'fallback'
                await cleanup()
                route = self_hosted(state.mock)
                fell_back = True
                continue
            if body.get('stream') and upstream.is_success:
                terminal = 'interrupted'

                def observe(usage, status):
                    nonlocal provider_usage, terminal
                    if isinstance(usage, dict):
                        provider_usage = usage
                    if status == 'done':
                        terminal = 'missing_usage' if provider_usage is None else 'completed'
                    elif status:
                        terminal = status

                relay = MeteredStream(upstream, cleanup, observe, state.stream_tasks,
                    timeout=float(os.getenv('STREAM_TIMEOUT', '300')))
                return relay.response({'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
                    'X-Inference-Provider': route.provider, 'X-Request-ID': usage_id or uuid.uuid4().hex})
            raw = await upstream.aread()
            if not upstream.is_success:
                terminal = 'provider_error'
                await cleanup()
                # Forward provider error details without exposing credentials.
                return Response(raw, status_code=upstream.status_code,
                    media_type='application/json', headers={'X-Inference-Provider': route.provider})
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError('Expected object')
            except ValueError:
                raise HTTPException(502, 'Provider returned an invalid response')
            provider_usage = data.get('usage') if isinstance(data.get('usage'), dict) else None
            terminal = 'missing_usage' if provider_usage is None else 'completed'
            await cleanup()
            if usage_id and (provider_usage is None or not valid_usage(provider_usage, maximum)):
                raise HTTPException(502, 'Provider returned invalid token accounting; no credits were charged')
            from .store import cost
            data['routing'] = routing
            data['metering'] = {'request_id': usage_id, 'mock': False,
                'cost_micro_usd': cost(provider_usage['prompt_tokens'], provider_usage['completion_tokens'],
                    route.input_rate, route.output_rate) if provider_usage else 0}
            return JSONResponse(data, headers={'X-Inference-Provider': route.provider})
        except BaseException as exc:
            await cleanup()
            # Connection failure can occur after generation starts; no blind retry
            # which might duplicate inference/cost. HTTP availability errors above
            # are the only automatic fallback trigger.
            if isinstance(exc, httpx.HTTPError):
                raise HTTPException(502, 'Model connection failed')
            raise


def valid_usage(usage, maximum):
    inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
    return type(inp) is int and type(out) is int and 0 <= inp <= int(os.getenv('MAX_INPUT_TOKENS', '12288')) and 0 <= out <= maximum
