import asyncio
import logging
import time


class Worker:
    """Single-process controller. Leases include warmup and the entire response stream."""
    def __init__(self, provider, idle_seconds=300, startup_timeout=900, capacity=8):
        self.provider = provider
        self.idle_seconds = idle_seconds
        self.startup_timeout = startup_timeout
        self.capacity = capacity
        self.lock = asyncio.Lock()
        self.active = 0
        self.last_used = time.monotonic()
        # Unknown on gateway restart: reap even a worker left running by a crash.
        self.state = 'unknown'
        self.warmup = None

    async def boot(self):
        async with self.lock:
            self.state = 'starting'
            try:
                await asyncio.wait_for(self._boot(), self.startup_timeout)
                self.state = 'ready'
            except BaseException:
                self.state = 'error'
                raise
            finally:
                self.last_used = time.monotonic()

    async def _boot(self):
        await self.provider.start()
        while not await self.provider.ready():
            await asyncio.sleep(1)

    async def acquire(self):
        # No await between admission check and increment on this event loop.
        if self.active >= self.capacity:
            raise OverflowError('Worker capacity reached')
        self.active += 1
        try:
            if self.warmup is None or self.warmup.done():
                self.warmup = asyncio.create_task(self.boot())
                self.warmup.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
            await asyncio.shield(self.warmup)
        except BaseException:
            self.release()
            raise

    def release(self):
        self.active -= 1
        self.last_used = time.monotonic()

    async def reap(self):
        async with self.lock:
            if self.active or self.state == 'stopped':
                return
            if time.monotonic() - self.last_used < self.idle_seconds:
                return
            self.state = 'stopping'
            try:
                await self.provider.stop()
                self.state = 'stopped'
            except Exception:
                self.state = 'error'
                raise

    async def watch(self):
        while True:
            await asyncio.sleep(min(10, self.idle_seconds))
            try:
                await self.reap()
            except Exception:
                logging.exception('Idle worker shutdown failed; will retry')
