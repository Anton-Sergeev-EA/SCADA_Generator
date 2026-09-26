// Пример для «Генератора»: совсем другая установка — котельная.
// Показывает, что мнемосхема строится для любого объекта, а не только для демо.
export const BOILER_YAML = `project:
  name: { ru: "Котельная №3", en: "Boiler House No. 3", zh: "3号锅炉房" }
  flow: [gas, boiler, circuit, consumers]
  groups:
    gas:       { ru: "Газ", en: "Gas supply", zh: "燃气" }
    boiler:    { ru: "Котёл", en: "Boiler", zh: "锅炉" }
    circuit:   { ru: "Контур", en: "Circuit", zh: "循环回路" }
    consumers: { ru: "Потребители", en: "Consumers", zh: "用户" }

devices:
  - id: boiler_plc
    host: 192.168.10.21
    port: 502
    tags:
      - { name: gas_pressure, label: { ru: "Давление газа", en: "Gas pressure", zh: "燃气压力" },
          address: 0, scale: 0.01, unit: "kPa", min: 0, max: 5, alarm_low: 1.5, alarm_high: 4.2, group: gas }
      - { name: gas_valve, label: { ru: "Отсечной клапан", en: "Shut-off valve", zh: "切断阀" },
          function: coil, address: 0, writable: true, group: gas }
      - { name: water_level, label: { ru: "Уровень в барабане", en: "Drum level", zh: "汽包水位" },
          address: 1, scale: 0.1, unit: "%", min: 0, max: 100, alarm_ll: 15, alarm_low: 30, alarm_high: 80, group: boiler }
      - { name: furnace_temp, label: { ru: "Температура топки", en: "Furnace temperature", zh: "炉膛温度" },
          address: 2, unit: "°C", min: 0, max: 1200, alarm_high: 1050, group: boiler }
      - { name: flue_o2, label: { ru: "Кислород в дымовых газах", en: "Flue gas O2", zh: "烟气含氧量" },
          address: 3, scale: 0.1, unit: "%", min: 0, max: 21, alarm_low: 2, group: boiler }
      - { name: circ_pump, label: { ru: "Циркуляционный насос", en: "Circulation pump", zh: "循环泵" },
          address: 4, unit: "rpm", min: 0, max: 2900, group: circuit }
      - { name: supply_temp, label: { ru: "Температура подачи", en: "Supply temperature", zh: "供水温度" },
          address: 5, scale: 0.1, unit: "°C", min: 0, max: 130, alarm_high: 115, group: circuit }
      - { name: return_temp, label: { ru: "Температура обратки", en: "Return temperature", zh: "回水温度" },
          address: 6, scale: 0.1, unit: "°C", min: 0, max: 100, group: circuit }
      - { name: heat_flow, label: { ru: "Расход теплоносителя", en: "Heating flow", zh: "热媒流量" },
          address: 7, scale: 0.1, unit: "m³/h", min: 0, max: 200, group: consumers }
      - { name: net_pressure, label: { ru: "Давление в сети", en: "Network pressure", zh: "管网压力" },
          address: 8, scale: 0.01, unit: "bar", min: 0, max: 10, alarm_low: 3, alarm_high: 8.5, group: consumers }
`;
