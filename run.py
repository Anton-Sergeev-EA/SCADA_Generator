#!/usr/bin/env python3
"""
SCADA Generator — точка входа.

    python run.py --demo          # всё в одном: модель установки + ML + интерфейс
    python run.py                 # реальные устройства из configs/config.yaml
    python run.py --no-web        # фоновый сервис без интерфейса (как в 0.x)
    python run.py --check         # проверка связи со всеми устройствами и выход
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
import webbrowser

from scada_core.config.loader import ConfigError, load_app_config
from scada_core.runtime import ScadaRuntime

logger = logging.getLogger("scada")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SCADA Generator")
    p.add_argument("--config", default=os.getenv("SCADA_CONFIG", "configs/config.yaml"))
    p.add_argument("--demo", action="store_true", help="встроенная модель насосной станции")
    p.add_argument("--no-web", action="store_true", help="без веб-интерфейса и API")
    p.add_argument("--no-db", action="store_true", help="не подключать PostgreSQL")
    p.add_argument("--host", default=None, help="адрес веб-сервера (по умолчанию из конфига)")
    p.add_argument("--port", type=int, default=None, help="порт веб-сервера")
    p.add_argument("--open", action="store_true", help="открыть интерфейс в браузере")
    p.add_argument(
        "--check", action="store_true", help="проверить связь с устройствами и выйти (0 — всё в порядке)"
    )
    p.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    return p.parse_args(argv)


async def serve(args: argparse.Namespace) -> None:
    os.environ["SCADA_CONFIG"] = args.config
    config = load_app_config(args.config)
    runtime = ScadaRuntime(config, demo=args.demo, use_db=False if args.no_db else None)
    await runtime.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows
            loop.add_signal_handler(sig, stop.set)

    server_task = None
    if not args.no_web:
        import uvicorn

        from scada_core.api.server import create_app

        host = args.host or config.server.host
        port = args.port or config.server.port
        if host not in ("127.0.0.1", "localhost") and not os.getenv("SCADA_API_TOKEN"):
            logger.warning(
                "Интерфейс доступен из сети без SCADA_API_TOKEN — любой сможет "
                "квитировать алармы и писать уставки. Задайте токен в .env."
            )
        server = uvicorn.Server(
            uvicorn.Config(create_app(runtime), host=host, port=port, log_level="warning")
        )
        server.install_signal_handlers = lambda: None  # сигналы обрабатываем сами
        server_task = asyncio.create_task(server.serve())
        url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
        logger.info("Интерфейс оператора: %s", url)
        if args.open:
            loop.call_later(1.5, webbrowser.open, url)

    logger.info("SCADA Generator запущен%s. Ctrl+C — остановка", " (демо)" if args.demo else "")
    try:
        if server_task:
            await asyncio.wait(
                {server_task, asyncio.create_task(stop.wait())}, return_when=asyncio.FIRST_COMPLETED
            )
        else:
            await stop.wait()
    finally:
        logger.info("Остановка системы...")
        if server_task:
            server.should_exit = True
            with contextlib.suppress(Exception):
                await server_task
        await runtime.stop()
        logger.info("Система остановлена")


def check(args: argparse.Namespace) -> int:
    from scada_core.commissioning import check_config, exit_code, format_report

    reports = asyncio.run(check_config(load_app_config(args.config)))
    print(format_report(reports))
    return exit_code(reports)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level="CRITICAL" if args.check else args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        if args.check:
            return check(args)
        asyncio.run(serve(args))
    except ConfigError as exc:
        logger.error("Ошибка в конфигурации:")
        for path, msg in exc.errors:
            logger.error("  %s: %s", path, msg)
        return 2
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
