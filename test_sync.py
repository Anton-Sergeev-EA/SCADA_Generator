#!/usr/bin/env python3
"""
Простой синхронный тест для проверки Modbus
"""
from pyModbusTCP.client import ModbusClient
import time

print("🔄 Подключение к Modbus эмулятору на localhost:5020...")

client = ModbusClient(host="localhost", port=5020, unit_id=1, timeout=5.0)

# Открываем соединение
if client.open():
    print("✅ Подключено к эмулятору!")
    
    # Небольшая задержка
    time.sleep(0.5)
    
    # Читаем регистры (адрес 0, 5 регистров)
    print("\n📊 Чтение Holding Registers...")
    regs = client.read_holding_registers(0, 5)
    if regs is not None:
        print(f"  Holding Registers (0-4): {regs}")
    else:
        print("  ❌ Ошибка чтения регистров (получен None)")
        print("  Возможная причина: неверный формат запроса")
    
    # Читаем катушки (адрес 0, 5 катушек)
    print("\n📊 Чтение Coils...")
    coils = client.read_coils(0, 5)
    if coils is not None:
        print(f"  Coils (0-4): {coils}")
    else:
        print("  ❌ Ошибка чтения катушек (получен None)")
    
    # Закрываем соединение
    client.close()
    print("\n✅ Тест завершен")
else:
    print("❌ Не удалось подключиться к эмулятору")
    print("   Убедитесь, что эмулятор запущен: python modbus_emulator_new.py")
