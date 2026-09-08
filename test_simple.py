import asyncio
import logging
from scada_core.engine.modbus_client import AsyncModbusManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test():
    """Test Modbus client functionality."""
    # Порт был 502 (стандартный Modbus-порт), но эмулятор
    # (modbus_emulator_new.py) по умолчанию слушает 5020 — с портом 502
    # этот скрипт никогда не смог бы подключиться к эмулятору.
    client = AsyncModbusManager("localhost", 5020, 1)
    
    if await client.connect():
        logger.info("Connected to emulator.")
        
        # Read registers.
        regs = await client.read_holding_registers(0, 5)
        if regs:
            logger.info(f"Holding Registers (0-4): {regs}")
        
        # Read coils.
        coils = await client.read_coils(0, 5)
        if coils:
            logger.info(f"Coils (0-4): {coils}")
        
        # Write value.
        success = await client.write_single_register(0, 999)
        if success:
            logger.info("Register 0 written = 999")
            # Verify write.
            reg = await client.read_holding_registers(0, 1)
            if reg:
                logger.info(f"Write verification: register 0 = {reg[0]}")
        
        await client.disconnect()
    else:
        logger.error("Failed to connect.")

if __name__ == "__main__":
    asyncio.run(test())

