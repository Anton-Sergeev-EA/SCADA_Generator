"""
Asynchronous Modbus TCP client with automatic reconnection
"""
import asyncio
import logging
from typing import Optional, List
from pyModbusTCP.client import ModbusClient

logger = logging.getLogger(__name__)

class ModbusConnectionError(Exception):
    """Modbus device connection error"""
    pass

class AsyncModbusManager:
    """
    Asynchronous Modbus TCP connection manager
    with automatic reconnection support
    """
    
    def __init__(
        self,
        host: str,
        port: int = 502,
        slave_id: int = 1,
        timeout: float = 3.0,
        max_retries: int = 3,
        reconnect_delay: float = 1.0
    ):
        """
        Initialize Modbus client
        
        Args:
            host: Device IP address or hostname
            port: Port (default 502)
            slave_id: Device ID (Unit ID)
            timeout: Timeout in seconds
            max_retries: Maximum number of connection attempts
            reconnect_delay: Initial delay between attempts
        """
        self.host = host
        self.port = port
        self.slave_id = slave_id
        self.timeout = timeout
        self.max_retries = max_retries
        self.reconnect_delay = reconnect_delay
        
        self.client = ModbusClient(
            host=host,
            port=port,
            unit_id=slave_id,
            timeout=timeout,
            auto_open=False
        )
        self._is_connected = False
        self._lock = asyncio.Lock()
    
    async def connect(self) -> bool:
        """
        Establish connection to the device.
        Returns True on success
        """
        async with self._lock:
            if self._is_connected:
                logger.info(f"Already connected to {self.host}:{self.port}")
                return True
            
            for attempt in range(self.max_retries):
                try:
                    logger.info(f"Attempting to connect to {self.host}:{self.port} (attempt {attempt + 1}/{self.max_retries})")
                    # Run in separate thread since client is synchronous
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, self.client.open)
                    
                    if result:
                        self._is_connected = True
                        logger.info(f"✅ Successfully connected to {self.host}:{self.port}")
                        return True
                    else:
                        logger.warning(f"Failed to connect to {self.host}:{self.port}")
                except Exception as e:
                    logger.error(f"Connection error: {e}")
                
                if attempt < self.max_retries - 1:
                    delay = self.reconnect_delay * (2 ** attempt)
                    logger.info(f"Retrying in {delay:.1f} seconds...")
                    await asyncio.sleep(delay)
            
            return False
    
    async def disconnect(self) -> None:
        """Close the connection"""
        async with self._lock:
            if self._is_connected:
                self.client.close()
                self._is_connected = False
                logger.info(f"Disconnected from {self.host}:{self.port}")
    
    async def _ensure_connection(self) -> bool:
        """Check connection and reconnect if necessary"""
        if not self._is_connected:
            return await self.connect()
        return True
    
    async def read_holding_registers(self, address: int, count: int = 1) -> Optional[List[int]]:
        """
        Read Holding Registers (function 0x03)
        
        Args:
            address: Starting address
            count: Number of registers
        
        Returns:
            List of values or None on error
        """
        try:
            if not await self._ensure_connection():
                return None
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                self.client.read_holding_registers, 
                address, 
                count
            )
            
            if result is not None:
                logger.debug(f"Read Holding Register: address={address}, value={result}")
                return result
            else:
                logger.error(f"Error reading Holding Register: address={address}")
                return None
                
        except Exception as e:
            logger.error(f"Exception while reading registers: {e}")
            self._is_connected = False
            return None
    
    async def read_coils(self, address: int, count: int = 1) -> Optional[List[bool]]:
        """
        Read Coils (function 0x01)
        
        Args:
            address: Starting address
            count: Number of coils
        
        Returns:
            List of boolean values or None on error
        """
        try:
            if not await self._ensure_connection():
                return None
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self.client.read_coils,
                address,
                count
            )
            
            if result is not None:
                logger.debug(f"Read Coil: address={address}, value={result}")
                return result
            else:
                logger.error(f"Error reading Coil: address={address}")
                return None
                
        except Exception as e:
            logger.error(f"Exception while reading coils: {e}")
            self._is_connected = False
            return None
    
    async def write_single_register(self, address: int, value: int) -> bool:
        """
        Write a single Holding Register (function 0x06)
        
        Args:
            address: Register address
            value: Value
        
        Returns:
            True on success
        """
        try:
            if not await self._ensure_connection():
                return False
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self.client.write_single_register,
                address,
                value
            )
            
            if result:
                logger.info(f"Write Holding Register: address={address}, value={value} ✅")
                return True
            else:
                logger.error(f"Error writing Holding Register: address={address}")
                return False
                
        except Exception as e:
            logger.error(f"Exception while writing register: {e}")
            self._is_connected = False
            return False
    
    @property
    def is_connected(self) -> bool:
        """Return connection status"""
        return self._is_connected
    
    def __repr__(self) -> str:
        return f"AsyncModbusManager(host={self.host}, port={self.port}, slave_id={self.slave_id})"

