"""
Modbus device polling scheduler
"""
import asyncio
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from scada_core.engine.modbus_client import AsyncModbusManager
from scada_core.config.loader import get_config

logger = logging.getLogger(__name__)

class TagValue:
    """Tag value with metadata"""
    def __init__(self, device_id: str, tag_name: str, value: Any, quality: str = "GOOD"):
        self.device_id = device_id
        self.tag_name = tag_name
        self.value = value
        self.timestamp = datetime.now()
        self.quality = quality

class DataPoller:
    """Modbus device polling scheduler"""
    
    def __init__(self):
        self._devices: Dict[str, AsyncModbusManager] = {}
        self._config = get_config()
        self._is_running = False
        self._poller_task = None
        self._alarm_callbacks = []
        self._data_callbacks = []
        
    def add_callback(self, callback_type: str, callback):
        """Add callback for data or alarm events"""
        if callback_type == "data":
            self._data_callbacks.append(callback)
        elif callback_type == "alarm":
            self._alarm_callbacks.append(callback)
    
    async def start(self):
        """Start the polling scheduler"""
        if self._is_running:
            return
        
        devices_config = self._config.get_devices()
        if not devices_config:
            logger.warning("No active devices configured")
            return
        
        for device_cfg in devices_config:
            client = AsyncModbusManager(
                host=device_cfg['host'],
                port=device_cfg.get('port', 502),
                slave_id=device_cfg.get('slave_id', 1),
                timeout=device_cfg.get('timeout', 3.0),
                max_retries=device_cfg.get('retries', 3)
            )
            self._devices[device_cfg['id']] = client
            logger.info(f"Device added: {device_cfg['name']}")
        
        self._is_running = True
        self._poller_task = asyncio.create_task(self._poll_loop())
        logger.info("DataPoller started")
    
    async def stop(self):
        """Stop the polling scheduler"""
        self._is_running = False
        if self._poller_task:
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
        
        for client in self._devices.values():
            await client.disconnect()
        
        logger.info("DataPoller stopped")
    
    async def _poll_loop(self):
        """Main polling loop"""
        while self._is_running:
            start_time = datetime.now()
            
            try:
                tasks = []
                for device_id, client in self._devices.items():
                    device_config = self._get_device_config(device_id)
                    if device_config:
                        tasks.append(self._poll_device(device_id, client, device_config))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Polling error: {result}")
                    elif result:
                        for callback in self._data_callbacks:
                            await callback(result)
                        
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")
            
            elapsed = (datetime.now() - start_time).total_seconds() * 1000
            await asyncio.sleep(max(0, 1000 - elapsed))
    
    async def _poll_device(self, device_id: str, client: AsyncModbusManager, config: dict):
        """Poll a single device"""
        try:
            if not await client.connect():
                return None
            
            tags = config.get('tags', [])
            if not tags:
                return None
            
            results = []
            for tag in tags:
                try:
                    value = await self._read_tag(client, tag)
                    if value is not None:
                        tag_value = TagValue(device_id, tag['name'], value)
                        results.append(tag_value)
                        await self._check_alarms(tag, value)
                except Exception as e:
                    logger.error(f"Error reading {tag['name']}: {e}")
            
            return results
            
        except Exception as e:
            logger.error(f"Device error for {device_id}: {e}")
            return None
    
    async def _read_tag(self, client: AsyncModbusManager, tag: dict) -> Optional[Any]:
        """Read a single tag from device"""
        function = tag.get('function', 'holding_register')
        address = tag.get('address', 0)
        count = tag.get('count', 1)
        
        if function == 'holding_register':
            raw_value = await client.read_holding_registers(address, count)
        elif function == 'coil':
            raw_value = await client.read_coils(address, count)
        else:
            return None
        
        if raw_value is None:
            return None
        
        scale = tag.get('scale', 1.0)
        if isinstance(raw_value, list):
            return raw_value[0] * scale if count == 1 else [v * scale for v in raw_value]
        return raw_value * scale
    
    async def _check_alarms(self, tag: dict, value: Any):
        """Check tag value against alarm thresholds"""
        if not self._alarm_callbacks:
            return
        
        if not isinstance(value, (int, float)):
            return
        
        alarm_low = tag.get('alarm_low')
        alarm_high = tag.get('alarm_high')
        
        if alarm_low is not None and value < alarm_low:
            for callback in self._alarm_callbacks:
                await callback(tag['name'], value, 'LOW', f"Below {alarm_low}")
        
        if alarm_high is not None and value > alarm_high:
            for callback in self._alarm_callbacks:
                await callback(tag['name'], value, 'HIGH', f"Above {alarm_high}")
    
    def _get_device_config(self, device_id: str) -> Optional[dict]:
        """Get device configuration by ID"""
        devices = self._config.get_devices()
        for device in devices:
            if device['id'] == device_id:
                return device
        return None

