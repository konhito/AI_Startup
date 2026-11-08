"""Keep provider accounting alive when a downstream SSE client disconnects."""
import asyncio
import json
import logging

from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse


class MeteredStream:
    def __init__(self, source, cleanup, observe, tasks, timeout=300, max_buffer_bytes=262144):
        self.source = source
        self.cleanup = cleanup
        self.observe = observe
        self.tasks = tasks
        self.timeout = timeout
        self.max_buffer_bytes = max_buffer_bytes
        self.queue = asyncio.Queue()
        self.buffer_bytes = 0
        self.attached = True
        self.overflow = False
        self.task = None

    def detach(self):
        self.attached = False
        while not self.queue.empty():
            self.queue.get_nowait()
        self.buffer_bytes = 0

    async def pump(self):
        try:
            await asyncio.wait_for(self.consume(), timeout=self.timeout)
        except asyncio.CancelledError:
            self.observe(None, 'interrupted')
            raise
        except (Exception, asyncio.TimeoutError):
            self.observe(None, 'provider_error')
            self.enqueue('data: ' + json.dumps({'error': {'message': 'Provider stream interrupted',
                'type': 'upstream_error'}}) + '\n\n')
            logging.warning('Provider stream interrupted; settling available usage')
        finally:
            try:
                await self.cleanup()
            finally:
                # Wakes a connected reader after all accounting is settled.
                self.queue.put_nowait(None)

    async def consume(self):
        pending = ''
        async for line in self.source.aiter_lines():
            if len(line.encode()) > 2 * 1024 * 1024:
                raise ValueError('Provider event exceeds size limit')
            if line.startswith('data:'):
                pending += line[5:].lstrip() + '\n'
                if len(pending.encode()) > 2 * 1024 * 1024:
                    raise ValueError('Provider event exceeds size limit')
            elif line == '' and pending:
                event = pending.strip()
                if event == '[DONE]':
                    self.observe(None, 'done')
                else:
                    try:
                        item = json.loads(event)
                        if isinstance(item, dict):
                            self.observe(item.get('usage'), 'provider_error' if item.get('error') else None)
                    except ValueError:
                        pass
                pending = ''
            self.enqueue(line + '\n')

    def enqueue(self, text):
        if not self.attached or self.overflow:
            return
        size = len(text.encode())
        if self.buffer_bytes + size > self.max_buffer_bytes:
            # A slow client must not keep a GPU lease or an unbounded buffer alive.
            self.overflow = True
            self.queue.put_nowait(None)
            return
        self.buffer_bytes += size
        self.queue.put_nowait(text)

    async def events(self):
        try:
            while True:
                item = await self.queue.get()
                if item is None:
                    if self.overflow:
                        yield 'data: ' + json.dumps({'error': {'message': 'Client stream buffer exceeded; '
                            'generation will finish and be accounted for', 'type': 'slow_client'}}) + '\n\n'
                    return
                self.buffer_bytes -= len(item.encode())
                yield item
        finally:
            # Do not cancel provider generation: drain its final usage within a
            # bounded deadline and settle even when the browser goes away.
            self.detach()

    def response(self, headers):
        self.task = asyncio.create_task(self.pump())
        self.tasks.add(self.task)

        def finished(task):
            self.tasks.discard(task)
            if not task.cancelled() and task.exception():
                logging.error('Stream cleanup failed', exc_info=(type(task.exception()),
                    task.exception(), task.exception().__traceback__))
        self.task.add_done_callback(finished)
        return StreamingResponse(self.events(), media_type='text/event-stream', headers=headers,
                                 background=BackgroundTask(self.detach))
