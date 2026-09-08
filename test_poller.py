#!/usr/bin/env python3
"""
DataPoller test with database storage
"""
import asyncio
import logging
from scada_core.engine.data_poller import DataPoller
from scada_core.database.repository import get_repository

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

repo = get_repository()

async def data_callback(results):
    """Save data to database"""
    try:
        for tag_value in results:
            await repo.save_tag_value(
                tag_value.device_id,
                tag_value.tag_name,
                tag_value.value,
                tag_value.quality
            )
            logger.info(f"{tag_value.device_id}/{tag_value.tag_name}: {tag_value.value}")
    except Exception as e:
        logger.error(f"Data save error: {e}")

async def alarm_callback(tag_name, value, alarm_type, message):
    """Save alarm to database"""
    try:
        await repo.save_alarm("plc_main", tag_name, alarm_type, value, message)
        logger.warning(f"{alarm_type}: {tag_name} = {value} - {message}")
    except Exception as e:
        logger.error(f"Alarm save error: {e}")

async def main():
    """Main entry point"""
    # Initialize database.
    try:
        await repo.initialize()
        logger.info("PostgreSQL ready")
    except Exception as e:
        logger.error(f"PostgreSQL error: {e}")
        return
    
    # Create Poller instance.
    poller = DataPoller()
    poller.add_callback("data", data_callback)
    poller.add_callback("alarm", alarm_callback)
    
    # Start polling.
    await poller.start()
    logger.info("DataPoller started. Press Ctrl+C to stop")
    
    try:
        await asyncio.sleep(30)  # Run for 30 seconds.
    except KeyboardInterrupt:
        pass
    finally:
        await poller.stop()
        await repo.close()
        logger.info("System stopped")

if __name__ == "__main__":
    asyncio.run(main())

