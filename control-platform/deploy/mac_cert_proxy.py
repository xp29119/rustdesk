#!/usr/bin/env python3
"""Loopback-only ACME CONNECT relay. No TLS interception or credential logging."""
import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re

HOSTS = frozenset(("acme-v02.api.letsencrypt.org", "acme-staging-v02.api.letsencrypt.org"))
LIMIT = 8192
LOG = logging.getLogger("rdcontrol-cert")


def authority(header):
    try:
        lines = header.decode("ascii").split("\r\n")
        method, target, version = lines[0].split(" ")
        if method != "CONNECT" or version not in ("HTTP/1.0", "HTTP/1.1"):
            return None
        match = re.fullmatch(r"([a-z0-9.-]+):443", target)
        if not match or match[1] not in HOSTS:
            return None
        seen_host = False
        for line in lines[1:]:
            if not line:
                continue
            name, value = line.split(":", 1)
            if not re.fullmatch(r"[!#$%&'*+\-.^_`|~A-Za-z0-9]+", name):
                return None
            name, value = name.lower(), value.strip()
            if name == "host":
                if seen_host or value != target:
                    return None
                seen_host = True
            if name == "transfer-encoding" or (name == "content-length" and value != "0"):
                return None
        return target
    except (ValueError, UnicodeError):
        return None


async def close(writer):
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), 2)
    except (OSError, asyncio.TimeoutError):
        pass


class Proxy:
    def __init__(self, upstream_port=6152):
        self.upstream_port = upstream_port
        self.active = 0

    async def handle(self, reader, writer):
        if self.active >= 16:
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n")
            await close(writer)
            return
        self.active += 1
        upstream_writer = None
        tasks = []
        connected = False
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            target = authority(header) if len(header) <= LIMIT else None
            if target is None:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self.upstream_port, limit=LIMIT), 8)
            # Rebuild from the fixed allowlist; incoming cookies/auth headers are never forwarded.
            upstream_writer.write(
                f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode("ascii"))
            await upstream_writer.drain()
            response = await asyncio.wait_for(upstream_reader.readuntil(b"\r\n\r\n"), 12)
            first = response.split(b"\r\n", 1)[0].split(b" ")
            if len(first) < 2 or first[0] not in (b"HTTP/1.0", b"HTTP/1.1") or first[1] != b"200":
                raise OSError("Upstream CONNECT rejected")
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            connected = True

            async def pump(source, destination):
                while True:
                    data = await asyncio.wait_for(source.read(65536), 90)
                    if not data:
                        return
                    destination.write(data)
                    await destination.drain()

            tasks = [asyncio.create_task(pump(reader, upstream_writer)),
                     asyncio.create_task(pump(upstream_reader, writer))]
            done, _ = await asyncio.wait(tasks, timeout=900, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                # Consume exceptions without logging raw HTTP input or TLS bytes.
                if not task.cancelled():
                    task.exception()
        except (OSError, ValueError, asyncio.TimeoutError,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            if not connected:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                try:
                    await writer.drain()
                except OSError:
                    pass
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if upstream_writer:
                await close(upstream_writer)
            await close(writer)
            self.active -= 1


async def serve(port, upstream_port):
    proxy = Proxy(upstream_port)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", port, limit=LIMIT)
    LOG.info("Certificate-only relay started on loopback port %s", port)
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18488)
    parser.add_argument("--upstream-port", type=int, default=6152)
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args()
    if not all(1024 <= port <= 65535 for port in (args.port, args.upstream_port)):
        parser.error("Use unprivileged loopback ports")
    if args.log_file:
        handler = RotatingFileHandler(args.log_file, maxBytes=65536, backupCount=1)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
    asyncio.run(serve(args.port, args.upstream_port))


if __name__ == "__main__":
    main()
