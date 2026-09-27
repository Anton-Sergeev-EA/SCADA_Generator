"""Клиентская сторона Modbus: запросы, разбор ответов, кадры RTU с CRC-16."""

from __future__ import annotations

import struct

READ_FUNCTIONS = {"coil": 0x01, "discrete_input": 0x02, "holding_register": 0x03, "input_register": 0x04}

EXCEPTIONS = {
    0x01: "illegal function",
    0x02: "illegal data address",
    0x03: "illegal data value",
    0x04: "slave device failure",
    0x06: "slave device busy",
    0x0B: "gateway target failed to respond",
}


class ModbusException(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"Modbus exception 0x{code:02X}: {EXCEPTIONS.get(code, 'unknown')}")
        self.code = code


def crc16(data: bytes) -> int:
    """CRC-16/MODBUS (полином 0xA001, начальное значение 0xFFFF)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def rtu_frame(unit: int, pdu: bytes) -> bytes:
    body = bytes([unit]) + pdu
    return body + struct.pack("<H", crc16(body))


def rtu_check(frame: bytes) -> bytes:
    """Проверяет CRC кадра RTU и возвращает unit + PDU."""
    if len(frame) < 4:
        raise ValueError("short RTU frame")
    body, crc = frame[:-2], struct.unpack("<H", frame[-2:])[0]
    if crc16(body) != crc:
        raise ValueError("RTU CRC mismatch")
    return body


def read_request(function: str, start: int, count: int) -> bytes:
    return struct.pack(">BHH", READ_FUNCTIONS[function], start, count)


def expected_length(pdu_head: bytes) -> int:
    """Длина PDU ответа по первым двум байтам (для чтения из потока RTU)."""
    fc = pdu_head[0]
    if fc & 0x80:
        return 2
    if fc in (0x01, 0x02, 0x03, 0x04):
        return 2 + pdu_head[1]
    if fc in (0x05, 0x06, 0x0F, 0x10):
        return 5
    raise ValueError(f"unexpected function code 0x{fc:02X}")


def parse_read(function: str, count: int, pdu: bytes) -> list[int] | list[bool]:
    fc = READ_FUNCTIONS[function]
    if pdu[0] == fc | 0x80:
        raise ModbusException(pdu[1])
    if pdu[0] != fc:
        raise ValueError("function code mismatch")
    data = pdu[2 : 2 + pdu[1]]
    if fc in (0x01, 0x02):
        return [bool((data[i // 8] >> (i % 8)) & 1) for i in range(count)]
    return list(struct.unpack(f">{count}H", data[: count * 2]))


def write_register_request(address: int, value: int) -> bytes:
    return struct.pack(">BHH", 0x06, address, value & 0xFFFF)


def write_registers_request(address: int, values: list[int]) -> bytes:
    return struct.pack(f">BHHB{len(values)}H", 0x10, address, len(values), len(values) * 2, *values)


def write_coil_request(address: int, value: bool) -> bytes:
    return struct.pack(">BHH", 0x05, address, 0xFF00 if value else 0x0000)


def check_write(request: bytes, pdu: bytes) -> None:
    if pdu[0] == request[0] | 0x80:
        raise ModbusException(pdu[1])
    if pdu[0] != request[0]:
        raise ValueError("function code mismatch")
