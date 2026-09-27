"""Минимальный MQTT 3.1.1 брокер для тестов и демонстраций.

Поддерживает CONNECT, SUBSCRIBE (+ и #), PUBLISH QoS 0/1, удерживаемые
(retained) сообщения, PINGREQ, DISCONNECT. Для эксплуатации используйте
полноценный брокер (Mosquitto, EMQX) — драйвер MQTT работает с любым.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct

from scada_core.drivers.mqtt import topic_matches


def _encode_len(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(data: bytes, i: int) -> tuple[str, int]:
    (n,) = struct.unpack(">H", data[i : i + 2])
    return data[i + 2 : i + 2 + n].decode(), i + 2 + n


class _Session:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.subscriptions: set[str] = set()

    def send(self, packet_type: int, flags: int, body: bytes) -> None:
        self.writer.write(bytes([(packet_type << 4) | flags]) + _encode_len(len(body)) + body)


class MiniMqttBroker:
    def __init__(self, host: str = "127.0.0.1", port: int = 1883) -> None:
        self.host, self.port = host, port
        self._server: asyncio.base_events.Server | None = None
        self._sessions: set[_Session] = set()
        self._tasks: set[asyncio.Task] = set()
        self.retained: dict[str, bytes] = {}
        self.published: list[tuple[str, bytes]] = []

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            for s in list(self._sessions):
                s.writer.close()
            for t in list(self._tasks):
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            await self._server.wait_closed()
            self._server = None

    def publish(self, topic: str, payload: bytes | str, retain: bool = False) -> None:
        data = payload.encode() if isinstance(payload, str) else payload
        if retain:
            self.retained[topic] = data
        for s in list(self._sessions):
            if any(topic_matches(p, topic) for p in s.subscriptions):
                self._deliver(s, topic, data)

    @staticmethod
    def _deliver(session: _Session, topic: str, payload: bytes) -> None:
        t = topic.encode()
        session.send(3, 0, struct.pack(">H", len(t)) + t + payload)

    async def _read_packet(self, reader: asyncio.StreamReader) -> tuple[int, int, bytes]:
        first = (await reader.readexactly(1))[0]
        mult, length = 1, 0
        while True:
            b = (await reader.readexactly(1))[0]
            length += (b & 0x7F) * mult
            if not b & 0x80:
                break
            mult *= 128
        return first >> 4, first & 0x0F, await reader.readexactly(length)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = _Session(writer)
        task = asyncio.current_task()
        if task:
            self._tasks.add(task)
        try:
            while True:
                ptype, flags, body = await self._read_packet(reader)
                if ptype == 1:  # CONNECT
                    self._sessions.add(session)
                    session.send(2, 0, b"\x00\x00")
                elif ptype == 3:  # PUBLISH
                    topic, i = _str(body, 0)
                    qos = (flags >> 1) & 3
                    if qos:
                        (pid,) = struct.unpack(">H", body[i : i + 2])
                        i += 2
                        session.send(4, 0, struct.pack(">H", pid))  # PUBACK
                    self.published.append((topic, body[i:]))
                    self.publish(topic, body[i:], retain=bool(flags & 1))
                elif ptype == 8:  # SUBSCRIBE
                    (pid,) = struct.unpack(">H", body[:2])
                    i, granted = 2, bytearray()
                    while i < len(body):
                        pattern, i = _str(body, i)
                        i += 1  # запрошенный QoS
                        session.subscriptions.add(pattern)
                        granted.append(0)
                    session.send(9, 0, struct.pack(">H", pid) + bytes(granted))
                    for topic, payload in self.retained.items():
                        if any(topic_matches(p, topic) for p in session.subscriptions):
                            self._deliver(session, topic, payload)
                elif ptype == 10:  # UNSUBSCRIBE
                    (pid,) = struct.unpack(">H", body[:2])
                    session.send(11, 0, struct.pack(">H", pid))
                elif ptype == 12:  # PINGREQ
                    session.send(13, 0, b"")
                elif ptype == 14:  # DISCONNECT
                    break
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._sessions.discard(session)
            if task:
                self._tasks.discard(task)
            with contextlib.suppress(Exception):
                writer.close()
