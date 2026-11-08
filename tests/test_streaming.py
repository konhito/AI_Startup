import asyncio

from app.streaming import MeteredStream


def test_disconnect_keeps_accounting_alive_until_final_usage():
    async def scenario():
        unblock = asyncio.Event()
        observed = []
        cleanup_count = []
        tasks = set()
        class Source:
            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"hello"}}]}'
                yield ''
                await unblock.wait()
                yield 'data: {"usage":{"prompt_tokens":100,"completion_tokens":20}}'
                yield ''
                yield 'data: [DONE]'
                yield ''
        async def cleanup():
            cleanup_count.append(1)
        relay = MeteredStream(Source(), cleanup, lambda u, s: observed.append((u, s)), tasks)
        response = relay.response({})
        assert 'hello' in await response.body_iterator.__anext__()
        await response.body_iterator.aclose()
        assert not cleanup_count and len(tasks) == 1
        unblock.set()
        await relay.task
        assert observed[-2][0] == {'prompt_tokens': 100, 'completion_tokens': 20}
        assert cleanup_count == [1]
    asyncio.run(scenario())


def test_slow_client_is_bounded_but_usage_still_observed():
    async def scenario():
        observed = []
        class Source:
            async def aiter_lines(self):
                for _ in range(100):
                    yield 'data: {"choices":[{"delta":{"content":"hello"}}]}'
                    yield ''
                yield 'data: {"usage":{"prompt_tokens":12,"completion_tokens":100}}'
                yield ''
                yield 'data: [DONE]'
                yield ''
        async def cleanup():
            pass
        relay = MeteredStream(Source(), cleanup, lambda u, s: observed.append((u, s)), set(), max_buffer_bytes=100)
        response = relay.response({})
        await relay.task
        assert relay.buffer_bytes <= 100
        text = ''.join([chunk async for chunk in response.body_iterator])
        assert 'slow_client' in text
        assert any(u == {'prompt_tokens': 12, 'completion_tokens': 100} for u, s in observed)
    asyncio.run(scenario())


def test_stream_timeout_releases_resources():
    async def scenario():
        observed, closed = [], []
        class Source:
            async def aiter_lines(self):
                await asyncio.sleep(10)
                yield ''
        async def cleanup():
            closed.append(True)
        relay = MeteredStream(Source(), cleanup, lambda u, s: observed.append((u, s)), set(), timeout=0.01)
        response = relay.response({})
        text = ''.join([chunk async for chunk in response.body_iterator])
        await relay.task
        assert 'upstream_error' in text and closed == [True]
        assert observed[-1][1] == 'provider_error'
    asyncio.run(scenario())
