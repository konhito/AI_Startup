"""Worker adapters. GCP operates an existing VM; it never provisions one."""
import asyncio
import os

import httpx


class MockProvider:
    def __init__(self):
        self.running = False
        self.starts = 0
        self.stops = 0

    async def start(self):
        self.starts += 1
        self.running = True

    async def ready(self):
        return self.running

    async def stop(self):
        self.stops += 1
        self.running = False


class GCPProvider:
    def __init__(self, client):
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        required = ('GCP_PROJECT', 'GCP_ZONE', 'GCP_INSTANCE', 'UPSTREAM_URL', 'UPSTREAM_API_KEY')
        for name in required:
            if not os.getenv(name):
                raise ValueError(f'{name} is required for GCP')
        credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
        self.session = AuthorizedSession(credentials)
        self.client = client
        self.base = ('https://compute.googleapis.com/compute/v1/projects/'
                     f"{os.environ['GCP_PROJECT']}/zones/{os.environ['GCP_ZONE']}")
        self.instance = f"{self.base}/instances/{os.environ['GCP_INSTANCE']}"

    async def request(self, method, url):
        response = await asyncio.to_thread(self.session.request, method, url, timeout=30)
        response.raise_for_status()
        return response.json()

    async def action(self, action):
        operation = await self.request('POST', f'{self.instance}/{action}')
        # Stop must finish before another request is allowed to restart the VM.
        for _ in range(180):
            result = await self.request('GET', f"{self.base}/operations/{operation['name']}")
            if result['status'] == 'DONE':
                if result.get('error'):
                    raise RuntimeError(f'GCP {action} operation failed')
                return
            await asyncio.sleep(2)
        raise TimeoutError(f'GCP {action} operation timed out')

    async def start(self):
        for _ in range(180):
            status = (await self.request('GET', self.instance))['status']
            if status == 'RUNNING':
                return
            if status == 'TERMINATED':
                await self.action('start')
                return
            await asyncio.sleep(2)
        raise TimeoutError('VM did not become startable')

    async def ready(self):
        try:
            response = await self.client.get(os.environ['UPSTREAM_URL'].rstrip('/') + '/health',
                headers={'Authorization': 'Bearer ' + os.environ['UPSTREAM_API_KEY']}, timeout=5)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def stop(self):
        status = (await self.request('GET', self.instance))['status']
        if status != 'TERMINATED':
            await self.action('stop')
