"""Adapt approved desktop registration to the OSS server's loopback UDP listener.

OSS hbbs accepts controller requests over WS but returns NOT_SUPPORT for RegisterPk.
Each registered WSS connection therefore owns one connected, loopback-only UDP socket.
Closing or revoking the WSS connection stops its registration heartbeat immediately.
"""
import asyncio
import socket

from aiohttp import WSMsgType
from google.protobuf.message import DecodeError

from .rendezvous_pb2 import RendezvousMessage

REGISTRATION_ADDRESS = ("127.0.0.1", 21116)
HEARTBEAT_SECONDS = 10


def registration_id(data):
    if len(data) > 4096:
        return None
    message = RendezvousMessage()
    try:
        message.ParseFromString(data)
    except DecodeError:
        return None
    if message.WhichOneof("union") != "register_pk":
        return None
    record = message.register_pk
    if not (6 <= len(record.id) <= 64 and 1 <= len(record.uuid) <= 128
            and len(record.pk) == 32 and not record.no_register_device):
        raise ValueError("Invalid desktop registration")
    return record.id


async def relay_registration(client, first, peer_id, counters):
    loop = asyncio.get_running_loop()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tasks = []
    try:
        udp.setblocking(False)
        udp.bind(("127.0.0.1", 0))
        # A connected socket accepts replies only from this fixed local hbbs endpoint.
        udp.connect(REGISTRATION_ADDRESS)
        await loop.sock_sendall(udp, first)
        counters[0] += len(first)
        heartbeat = RendezvousMessage()
        heartbeat.register_peer.id = peer_id
        heartbeat_bytes = heartbeat.SerializeToString()

        async def receive_client():
            async for message in client:
                if message.type != WSMsgType.BINARY:
                    await client.close(code=1003, message=b"Binary transport required")
                    return
                if not message.data:
                    continue
                # An ID change reconnects through the ordinary client retry path.
                if registration_id(message.data) != peer_id:
                    await client.close(code=1008, message=b"Registration changed; reconnect")
                    return
                await loop.sock_sendall(udp, message.data)
                counters[0] += len(message.data)

        async def receive_server():
            while True:
                data = await loop.sock_recv(udp, 65536)
                if data:
                    counters[1] += len(data)
                    await client.send_bytes(data)

        async def keep_registered():
            while True:
                await loop.sock_sendall(udp, heartbeat_bytes)
                await asyncio.sleep(HEARTBEAT_SECONDS)

        tasks = [asyncio.create_task(receive_client()),
                 asyncio.create_task(receive_server()),
                 asyncio.create_task(keep_registered())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        udp.close()
