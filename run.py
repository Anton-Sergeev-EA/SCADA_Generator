#!/usr/bin/env python3
"""
Запуск SCADA системы как сервиса
"""
import asyncio
import logging
import signal
import sys
from scada_core.engine.data_poller import DataPoller
from scada_core.database.repository import get_repository

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

repo = get_repository()
poller = DataPoller()

async def data_callback(results):
    for tag_value in results:
        try:
            await repo.save_tag_value(
                tag_value.device_id,
                tag_value.tag_name,
                tag_value.value,
                tag_value.quality
            )
            logger.info(f"📊 {tag_value.device_id}/{tag_value.tag_name}: {tag_value.value}")
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")

async def alarm_callback(tag_name, value, alarm_type, message):
    try:
        await repo.save_alarm("plc_main", tag_name, alarm_type, value, message)
        logger.warning(f"🚨 {alarm_type}: {tag_name} = {value} - {message}")
    except Exception as e:
        logger.error(f"Ошибка сохранения аларма: {e}")

async def main():
    # Настройка обработки сигналов
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))
    
    try:
        await repo.initialize()
        logger.info("✅ PostgreSQL подключен")
        
        poller.add_callback("data", data_callback)
        poller.add_callback("alarm", alarm_callback)
        
        await poller.start()
        logger.info("🚀 SCADA Generator запущен!")
        logger.info("Нажмите Ctrl+C для остановки")
        
        # Бесконечный цикл
        while True:
            await asyncio.sleep(1)
            
    except asyncio.CancelledError:
        pass
    finally:
        await shutdown()

async def shutdown():
    logger.info("🛑 Остановка системы...")
    await poller.stop()
    await repo.close()
    logger.info("✅ Система остановлена")
    sys.exit(0)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
