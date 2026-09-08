#!/usr/bin/env python3
"""
Modbus TCP эмулятор с поддержкой основных функций
Рабочая версия
"""
import struct
import socket
import threading
import time
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ModbusEmulator:
    def __init__(self, host='0.0.0.0', port=5020):
        self.host = host
        self.port = port
        self.running = False
        self.server = None
        
        # Данные устройства
        self.holding_registers = [100, 150, 200, 250, 300, 350, 400, 450, 500, 550]
        self.coils = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0]  # 1=True, 0=False
        self.discrete_inputs = [0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
        self.input_registers = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        
        self.counter = 0
    
    def start(self):
        """Запускает сервер"""
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((self.host, self.port))
        self.server.listen(5)
        self.running = True
        
        logger.info(f"✅ Modbus TCP эмулятор запущен на {self.host}:{self.port}")
        logger.info("📊 Данные эмулятора:")
        logger.info(f"  Holding Registers (0-4): {self.holding_registers[:5]}")
        logger.info(f"  Coils (0-4): {self.coils[:5]}")
        logger.info("Нажмите Ctrl+C для остановки")
        
        # Поток обновления данных
        update_thread = threading.Thread(target=self._update_data, daemon=True)
        update_thread.start()
        
        try:
            while self.running:
                conn, addr = self.server.accept()
                logger.info(f"Клиент подключен: {addr}")
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True
                )
                client_thread.start()
        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            logger.error(f"Ошибка сервера: {e}")
            self.stop()
    
    def stop(self):
        """Останавливает сервер"""
        self.running = False
        if self.server:
            self.server.close()
        logger.info("✅ Эмулятор остановлен")
    
    def _update_data(self):
        """Обновляет данные для демонстрации"""
        while self.running:
            self.counter += 1
            # Меняем температуру (регистр 0)
            self.holding_registers[0] = 50 + (self.counter % 200)
            # Меняем давление (регистр 1)
            self.holding_registers[1] = self.counter % 200
            time.sleep(1)
    
    def _handle_client(self, conn, addr):
        """Обрабатывает клиента"""
        try:
            while self.running:
                data = conn.recv(1024)
                if not data:
                    break
                
                logger.debug(f"Получено {len(data)} байт: {data.hex()}")
                
                response = self._process_request(data)
                if response:
                    logger.debug(f"Отправлено {len(response)} байт: {response.hex()}")
                    conn.send(response)
                else:
                    logger.warning("Нет ответа на запрос")
                    break
                    
        except Exception as e:
            logger.error(f"Ошибка с клиентом {addr}: {e}")
        finally:
            conn.close()
            logger.info(f"Клиент отключен: {addr}")
    
    def _process_request(self, data):
        """Обрабатывает Modbus запрос"""
        if len(data) < 8:
            return None
        
        # Парсим заголовок
        transaction_id = data[0:2]
        protocol_id = data[2:4]
        length = (data[4] << 8) | data[5]
        unit_id = data[6]
        function_code = data[7]
        
        logger.info(f"Запрос: функция 0x{function_code:02X}, данные: {data.hex()}")
        
        # Формируем ответ
        response = bytearray()
        response.extend(transaction_id)
        response.extend(protocol_id)
        response.append(0)  # Длина (заполним позже)
        response.append(0)
        response.append(unit_id)
        response.append(function_code)
        
        # Обработка разных функций
        if function_code == 0x03:  # Read Holding Registers
            if len(data) >= 12:
                start_addr = (data[8] << 8) | data[9]
                quantity = (data[10] << 8) | data[11]
                
                logger.info(f"Чтение Holding Registers: адрес={start_addr}, кол-во={quantity}")
                
                # Добавляем данные
                response.append(quantity * 2)  # Количество байт данных
                
                for i in range(quantity):
                    idx = start_addr + i
                    if idx < len(self.holding_registers):
                        value = self.holding_registers[idx]
                        response.append((value >> 8) & 0xFF)
                        response.append(value & 0xFF)
                    else:
                        response.append(0)
                        response.append(0)
                
                # Обновляем длину
                length = len(response) - 6
                response[4] = (length >> 8) & 0xFF
                response[5] = length & 0xFF
                
                return bytes(response)
        
        elif function_code == 0x01:  # Read Coils
            if len(data) >= 12:
                start_addr = (data[8] << 8) | data[9]
                quantity = (data[10] << 8) | data[11]
                
                logger.info(f"Чтение Coils: адрес={start_addr}, кол-во={quantity}")
                
                byte_count = (quantity + 7) // 8
                response.append(byte_count)
                
                # Упаковываем биты
                bits = []
                for i in range(quantity):
                    idx = start_addr + i
                    if idx < len(self.coils):
                        bits.append(self.coils[idx])
                    else:
                        bits.append(0)
                
                # Упаковываем в байты
                for i in range(0, len(bits), 8):
                    byte = 0
                    for j in range(8):
                        if i + j < len(bits) and bits[i + j]:
                            byte |= (1 << j)
                    response.append(byte)
                
                # Обновляем длину
                length = len(response) - 6
                response[4] = (length >> 8) & 0xFF
                response[5] = length & 0xFF
                
                return bytes(response)
        
        elif function_code == 0x06:  # Write Single Register
            if len(data) >= 12:
                address = (data[8] << 8) | data[9]
                value = (data[10] << 8) | data[11]
                
                if address < len(self.holding_registers):
                    self.holding_registers[address] = value
                    logger.info(f"✏️ Запись регистра {address} = {value}")
                    
                    # Эхо-ответ (возвращаем те же данные)
                    response.extend(data[8:12])
                    
                    # Обновляем длину
                    length = len(response) - 6
                    response[4] = (length >> 8) & 0xFF
                    response[5] = length & 0xFF
                    
                    return bytes(response)
        
        elif function_code == 0x05:  # Write Single Coil
            if len(data) >= 12:
                address = (data[8] << 8) | data[9]
                value = (data[10] << 8) | data[11]
                
                if address < len(self.coils):
                    self.coils[address] = 1 if value == 0xFF00 else 0
                    logger.info(f"✏️ Запись катушки {address} = {self.coils[address]}")
                    
                    # Эхо-ответ
                    response.extend(data[8:12])
                    
                    # Обновляем длину
                    length = len(response) - 6
                    response[4] = (length >> 8) & 0xFF
                    response[5] = length & 0xFF
                    
                    return bytes(response)
        
        elif function_code == 0x02:  # Read Discrete Inputs
            if len(data) >= 12:
                start_addr = (data[8] << 8) | data[9]
                quantity = (data[10] << 8) | data[11]
                
                logger.info(f"Чтение Discrete Inputs: адрес={start_addr}, кол-во={quantity}")
                
                byte_count = (quantity + 7) // 8
                response.append(byte_count)
                
                bits = []
                for i in range(quantity):
                    idx = start_addr + i
                    if idx < len(self.discrete_inputs):
                        bits.append(self.discrete_inputs[idx])
                    else:
                        bits.append(0)
                
                for i in range(0, len(bits), 8):
                    byte = 0
                    for j in range(8):
                        if i + j < len(bits) and bits[i + j]:
                            byte |= (1 << j)
                    response.append(byte)
                
                # Обновляем длину
                length = len(response) - 6
                response[4] = (length >> 8) & 0xFF
                response[5] = length & 0xFF
                
                return bytes(response)
        
        elif function_code == 0x04:  # Read Input Registers
            if len(data) >= 12:
                start_addr = (data[8] << 8) | data[9]
                quantity = (data[10] << 8) | data[11]
                
                logger.info(f"Чтение Input Registers: адрес={start_addr}, кол-во={quantity}")
                
                response.append(quantity * 2)
                
                for i in range(quantity):
                    idx = start_addr + i
                    if idx < len(self.input_registers):
                        value = self.input_registers[idx]
                        response.append((value >> 8) & 0xFF)
                        response.append(value & 0xFF)
                    else:
                        response.append(0)
                        response.append(0)
                
                # Обновляем длину
                length = len(response) - 6
                response[4] = (length >> 8) & 0xFF
                response[5] = length & 0xFF
                
                return bytes(response)
        
        # Функция не поддерживается
        logger.warning(f"❌ Неподдерживаемая функция: 0x{function_code:02X}")
        return None

def main():
    emulator = ModbusEmulator()
    try:
        emulator.start()
    except KeyboardInterrupt:
        emulator.stop()

if __name__ == "__main__":
    main()

