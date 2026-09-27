"""Эмулятор контролируемого пункта (КП) МЭК 60870-5-104 для тестов и пусконаладки.

Работает отдельным процессом, как настоящая станция: если запустить её в одном
процессе с клиентом, обработчики обеих сторон делят GIL и подтверждения команд
приходят с задержкой.

    python -m scada_core.sim.iec104_station --port 2404 --ca 1 \\
        --point 1001:float:12.5 --point 2001:bool:1 --command 5001:float

Команды построчно в stdin:  set <ioa> <value>  — изменить и передать спорадически,
                            quiet <ioa> <value> — изменить без передачи.
В stdout: `ready`, `ok <ioa>` после применения каждой строки из stdin и
`cmd <ioa> <value>` на каждую принятую команду.
"""

# Без `from __future__ import annotations`: c104 проверяет аннотации обработчика команд.
import argparse
import asyncio
import sys
import threading

_MONITOR = {"float": "M_ME_NC_1", "int16": "M_ME_NB_1", "bool": "M_SP_NA_1"}
_COMMAND = {"float": "C_SE_NC_1", "int16": "C_SE_NB_1", "bool": "C_SC_NA_1"}


def _value(c104, kind: str, text: str):
    if kind == "bool":
        return text.lower() in ("1", "true", "on")
    if kind == "int16":
        return c104.Int16(int(float(text)))
    return float(text)


def main(argv: list[str] | None = None) -> int:
    import c104

    p = argparse.ArgumentParser(description="IEC 60870-5-104 station emulator")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2404)
    p.add_argument("--ca", type=int, default=1, help="common address")
    p.add_argument("--point", action="append", default=[], help="ioa:float|int16|bool:value")
    p.add_argument("--command", action="append", default=[], help="ioa:float|int16|bool")
    args = p.parse_args(argv)

    server = c104.Server(ip=args.host, port=args.port)
    station = server.add_station(common_address=args.ca)
    points: dict[int, tuple[object, str]] = {}
    for spec in args.point:
        ioa, kind, value = spec.split(":")
        point = station.add_point(io_address=int(ioa), type=getattr(c104.Type, _MONITOR[kind]))
        point.value = _value(c104, kind, value)
        points[int(ioa)] = (point, kind)
    lock = threading.Lock()

    def on_command(
        point: c104.Point, previous_info: c104.Information, message: c104.IncomingMessage
    ) -> c104.ResponseState:
        with lock:
            print(f"cmd {point.io_address} {float(point.value)!r}", flush=True)
        return c104.ResponseState.SUCCESS

    for spec in args.command:
        ioa, kind = spec.split(":")
        cmd = station.add_point(io_address=int(ioa), type=getattr(c104.Type, _COMMAND[kind]))
        cmd.on_receive(callable=on_command)

    server.start()
    with lock:
        print("ready", flush=True)
    try:
        for line in sys.stdin:
            parts = line.split()
            if len(parts) != 3 or parts[0] not in ("set", "quiet"):
                continue
            point, kind = points[int(parts[1])]
            point.value = _value(c104, kind, parts[2])
            if parts[0] == "set":
                point.transmit(cause=c104.Cot.SPONTANEOUS)
            with lock:
                print(f"ok {parts[1]}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


class StationProcess:
    """Асинхронная обёртка для тестов: запуск, изменение значений, принятые команды."""

    def __init__(self, port: int, ca: int, points: list[str], commands: list[str]) -> None:
        self.argv = ["--port", str(port), "--ca", str(ca)]
        for spec in points:
            self.argv += ["--point", spec]
        for spec in commands:
            self.argv += ["--command", spec]
        self.commands: list[tuple[int, float]] = []
        self._acks: asyncio.Queue[int] = asyncio.Queue()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None

    async def start(self, timeout: float = 10.0) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "scada_core.sim.iec104_station",
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        line = await asyncio.wait_for(self._proc.stdout.readline(), timeout)
        if line.strip() != b"ready":
            raise RuntimeError(f"IEC 104 station did not start: {line!r}")
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self._proc and self._proc.stdout
        async for raw in self._proc.stdout:
            parts = raw.decode().split()
            if len(parts) == 3 and parts[0] == "cmd":
                self.commands.append((int(parts[1]), float(parts[2])))
            elif len(parts) == 2 and parts[0] == "ok":
                self._acks.put_nowait(int(parts[1]))

    async def set(self, ioa: int, value: float, transmit: bool = True, timeout: float = 10.0) -> None:
        """Возвращается, когда станция применила значение."""
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(f"{'set' if transmit else 'quiet'} {ioa} {value}\n".encode())
        await self._proc.stdin.drain()
        while await asyncio.wait_for(self._acks.get(), timeout) != ioa:
            pass

    async def stop(self) -> None:
        if self._proc is None:
            return
        proc, self._proc = self._proc, None
        if proc.stdin:
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        if self._reader:
            await self._reader


if __name__ == "__main__":
    sys.exit(main())
