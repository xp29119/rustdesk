import asyncio
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "mac_cert_proxy", Path(__file__).parents[1] / "deploy/mac_cert_proxy.py")
proxy_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy_module)


async def test_allowlist_rejects_other_hosts_methods_ports_and_ambiguous_requests():
    seen = []

    async def upstream(reader, writer):
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    proxy = proxy_module.Proxy(server.sockets[0].getsockname()[1])
    entrance = await asyncio.start_server(proxy.handle, "127.0.0.1", 0, limit=8192)
    port = entrance.sockets[0].getsockname()[1]
    try:
        for request in [
            b"GET https://acme-v02.api.letsencrypt.org/directory HTTP/1.1\r\n\r\n",
            b"CONNECT example.com:443 HTTP/1.1\r\n\r\n",
            b"CONNECT 172.65.32.248:443 HTTP/1.1\r\n\r\n",
            b"CONNECT acme-v02.api.letsencrypt.org:80 HTTP/1.1\r\n\r\n",
            b"CONNECT acme-v02.api.letsencrypt.org.evil.test:443 HTTP/1.1\r\n\r\n",
            b"CONNECT acme-v02.api.letsencrypt.org:443 HTTP/1.1\r\nHost: evil.test:443\r\n\r\n",
            b"CONNECT acme-v02.api.letsencrypt.org:443 HTTP/1.1\r\nContent-Length: 1\r\n\r\nx",
        ]:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(request)
            await writer.drain()
            result = await asyncio.wait_for(reader.read(), 2)
            assert result.startswith(b"HTTP/1.1 403"), result
            writer.close()
            await writer.wait_closed()
        assert seen == []
    finally:
        entrance.close()
        server.close()
        await entrance.wait_closed()
        await server.wait_closed()


async def test_allowed_tunnel_preserves_bytes_and_drops_incoming_identity_headers():
    seen = []
    finished = asyncio.Event()

    async def upstream(reader, writer):
        try:
            seen.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    proxy = proxy_module.Proxy(server.sockets[0].getsockname()[1])
    entrance = await asyncio.start_server(proxy.handle, "127.0.0.1", 0, limit=8192)
    try:
        for host in sorted(proxy_module.HOSTS):
            finished.clear()
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", entrance.sockets[0].getsockname()[1])
            request = (f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n"
                       "Cookie: do-not-forward\r\nAuthorization: secret-test-value\r\n\r\n")
            # Include pipelined binary bytes to verify they are not lost after parsing CONNECT.
            payload = bytes(range(256)) * 1000
            writer.write(request.encode() + payload)
            await writer.drain()
            assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
            assert await asyncio.wait_for(reader.readexactly(len(payload)), 3) == payload
            writer.close()
            await writer.wait_closed()
            await asyncio.wait_for(finished.wait(), 2)
            assert seen[-1] == f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode()
    finally:
        entrance.close()
        server.close()
        await entrance.wait_closed()
        await server.wait_closed()
