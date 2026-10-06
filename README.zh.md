# SCADA Generator

[Русский](README.md) · [English](README.en.md) · **中文** · [हिन्दी](README.hi.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Italiano](README.it.md)

> 根据装置的 YAML 描述**自动生成**，并在报警触发前数分钟**提前预警**的 SCADA。

传统 SCADA 需要数周手工绘制工艺画面，而阈值报警往往在为时已晚时才触发。
SCADA Generator 的做法不同：

| | 传统 SCADA | SCADA Generator |
|---|---|---|
| 操作员界面 | 在编辑器中手工绘制 | 由 `config.yaml` **自动生成**：单元按工艺流程排列，元件按信号类型选择 |
| 操作员何时发现问题 | 数值越限时 | **预测报警**：“振动将在 3 分 12 秒后达到高限” |
| 阈值无法发现的故障（泄漏、传感器漂移） | 无法发现 | **PCA 模型**发现信号间关联被破坏，并指出责任位号 |
| 启停时的报警泛滥 | 数十条报警 | **按根因分组**：一个“工况切换”事件 |
| 报警管理 | “每秒写一次数据库” | **ISA-18.2** 状态模型、回差、延时、确认、搁置、**EEMUA 191** 指标 |
| 设备 | 单一协议或厂商专用软件 | **Modbus TCP/RTU、OPC UA、MQTT、IEC 104** — 任何厂商，可混用协议 |
| 分析 | Python / 云端 | **C++17 内核**（pybind11），从 Python 调用可达每秒 450 万样本，可在隔离网络运行 |
| 语言 | 一种 | **俄语、英语、中文** — 界面、AI 解释、位号名称 |
| 外观 | 单一主题 | **浅色、深色或跟随系统** — 顶栏切换，自动记住选择 |

## 快速开始 — 一分钟演示

```bash
pip install -r requirements.txt
python run.py --demo --open
```

浏览器将打开 `http://127.0.0.1:8000`。演示模式会启动泵站物理模型（储罐、带 PI 液位
调节的泵、变化的用户用水）和真实的 Modbus TCP 服务器。ML 模型预先用 10 分钟正常运行
数据训练，无需 PostgreSQL。

在 **“演示场景”** 页面可以向模型注入故障，观察 AI 如何先于阈值报警发现问题：

| 场景 | AI 发现 | 发现时间（故障开始后） | 阈值报警 |
|---|---|---|---|
| 轴承磨损 | 关联破坏 → 振动漂移 → **预测报警**“约 3 分钟后达到高限” | 56 秒 / 74 秒 / 173 秒 | 振动高限：341 秒后 |
| 泵紧急停机 | 一个“工况切换”事件 + **预测报警**“约 4.5 分钟后液位达到低限” | 0 秒 / 114 秒 | 液位低限：384 秒后 |
| 储罐泄漏 | 关联破坏（进出水平衡） | 18 秒 | **永远不会触发** |
| 温度传感器漂移 | `bearing_temp` 漂移，贡献 86–90 % | 63 秒 | 10 分钟内不触发 |
| 压力传感器卡死 | “数值不变 — 疑似传感器卡死” | 14 秒 | 永远不会触发 |
| 电流回路干扰 | 趋势图上标出突变 | 1–9 秒 | — |

以上数据来自与自动化测试（`tests/test_ml.py`）相同模型上的运行结果。正常运行 10 分钟内
没有预测报警和漂移，只可能出现个别噪声突变。

## 工作原理

- **流式检测器（C++，每个位号 O(1) 内存）**：带 Huber 截断的鲁棒 EWMA 模型给出预期值
  和趋势图上的“正常区间”；检测突变、缓慢漂移（带回差，不会在周期性等自相关信号上误报）
  和传感器卡死；数值处于量程边界（0、min、max）时视为设备停运，而非卡死。
- **“距报警时间”预测（C++）**：支持不等间隔采样的 Holt 双指数平滑。只有当前趋势对该
  位号来说异常时才发出预测；突变不会扭曲趋势；外推距离不超过已观察趋势持续时间的 4 倍，
  因此调节器已消除的工况阶跃不会变成误报。
- **多变量模型（PCA-MSPC）**：学习装置信号如何共同变化；SPE 表示“新模式”，T² 表示
  “熟悉但过强”的偏离。位号贡献采用组合指标上的基于重构的贡献（RBC，Alcala & Qin；
  Yue & Qin），能准确指出真正的责任位号。
- **C++ 与 Python 结果一致**：每个内核算法都在 `scada_core/ml/fallback.py` 中有镜像
  实现，`tests/test_native_parity.py` 验证误差小于 1e-9。没有编译器时系统在 Python
  内核上运行，速度较慢但结果相同。

| 检测器吞吐量 | 样本/秒 |
|---|---|
| 纯 C++（`DetectorBank`，1000 个位号） | 约 4100 万 |
| 从 Python 调用 C++（每个轮询周期一次调用，释放 GIL） | 约 450 万 |
| Python 备用实现 | 约 40 万 |

## 配置

所有内容都在 `configs/config.yaml` 中描述。最小位号只需 `name` 和 `address`；字段与
俄文 README 相同：`type`、`scale`、`offset`、`unit`、`min`、`max`、`alarm_hh`、
`alarm_high`、`alarm_low`、`alarm_ll`、`deadband`、`on_delay_s`、`group`、`widget`、
`writable`、`ml`、`label: {ru, en, zh}`。启动时会校验配置，并逐条给出带路径的错误。配置值可取自环境变量：`host: ${PLC_HOST:-localhost}`。
在 **“生成器”** 页面可粘贴任意装置的 YAML（内置锅炉房示例），立即查看工艺画面并下载
SVG 或 JSON。

## 协议：任何厂商的设备

SCADA Generator 不绑定任何厂商。设备通过开放标准接入，同一装置中可以混用多种协议，报警、AI、
工艺画面和归档对所有设备的工作方式完全相同。协议只需在设备上写一行。

| `protocol` | 用途 | 位号地址 | 库（许可证） |
|---|---|---|---|
| `modbus_tcp` | 以太网 PLC、网关、变频器 | `address` + `function` | pyModbusTCP（MIT），内置 |
| `modbus_rtu` | RS-485/RS-232、RTU over TCP 网关（`socket://`）、RFC 2217 | `address` + `function` | pyserial（BSD） |
| `opcua` | 任意厂商的 OPC UA（IEC 62541）PLC 和服务器 | `node: "ns=2;s=…"` | asyncua（LGPL-3.0） |
| `mqtt` | IIoT 网关、无线传感器、Mosquitto/EMQX | `topic`（+ `json_path`） | aiomqtt（BSD） |
| `iec104` | 远动、变电站（IEC 60870-5-104） | `ioa` | c104（GPL-3.0） |

驱动为可选项：只安装需要的（`pip install asyncua`，或 `pip install -r requirements-protocols.txt`
全部安装）。缺少库的设备显示为离线并给出原因，其余设备照常工作。五种协议的完整示例见
`configs/examples/multi_protocol.yaml`。每个驱动都针对真实服务器或仿真器测试：读、写、设备错误、
断线与恢复。无设备调试时可使用 IEC 104 子站仿真器：
`python -m scada_core.sim.iec104_station --port 2404 --ca 1 --point 1001:float:10.5 --command 5001:float`
（输入 `set 1001 11.2` 修改数值，收到的命令会打印出来）。

## Docker

```bash
docker build -t scada-generator .
docker run --rm -p 127.0.0.1:8000:8000 scada-generator        # 演示
SCADA_API_TOKEN=secret DB_PASSWORD=pass docker compose up --build
```

`docker compose` 启动完整环境：PLC 仿真器、PostgreSQL 和生产模式的 SCADA。用于真实装置时
删除 `plc-sim` 服务并设置 `PLC_HOST`/`PLC_PORT`。镜像会构建并测试 C++ 内核，以非特权用户
运行，并带有 `HEALTHCHECK`。

## 生产环境

```bash
cp .env.example .env          # DB_*、SCADA_API_TOKEN、设备地址
pip install -r requirements-protocols.txt   # RTU、OPC UA、MQTT、IEC 104 驱动（按需）
python run.py --check         # 检查与每台设备的通信，不写入任何数据
python run.py                 # 轮询 + 归档 + 界面
python run.py --no-web        # 无界面后台服务
python run.py --no-db         # 不使用 PostgreSQL
```

- **调试投运**：`--check` 连接配置中的每台设备并读取全部位号，一切正常时返回 0，否则返回 1，
  并为每台设备给出一行原因，适用于现场验收和部署脚本。
- **PostgreSQL**：版本化迁移。0.x 版本的数据库会自动升级：时间转换为 `TIMESTAMPTZ`
  且不丢失时刻，增加 `(tag_id, timestamp)` 索引，每次报警激活只占一行。
- **安全**：默认只监听 `127.0.0.1`；设置 `SCADA_API_TOKEN` 后，控制命令需要
  `X-API-Token` 请求头；只允许写入 `writable: true` 且在 `min..max` 范围内的位号；
  界面不依赖 CDN 和外部字体，可在隔离的工控网络中使用。
- **监控**：`/api/health` 返回 200 或 503（设备或数据库不可用，原因见 `device_errors`），`/api/health/live` 为存活探针。
- **API**：见俄文 README 中的表格，或访问 `/docs` 查看交互式文档。

## 构建与测试

```bash
pip install -r requirements-dev.txt
python scripts/build_native.py                   # CMake + ctest
python -m pytest -q                              # 88 个测试，仿真器在测试内启动
SCADA_FORCE_PYTHON_CORE=1 python -m pytest -q    # 在 Python 内核上运行相同测试
```

## 当前限制

- 协议：Modbus TCP/RTU、OPC UA、MQTT、IEC 60870-5-104。尚不支持 BACnet、PROFINET、EtherNet/IP，
  此类设备通常通过 OPC UA 网关接入。未实现西门子私有的 S7 协议：S7-1200（固件 4.4+）和 S7-1500
  内置 OPC UA 服务器，其他型号可使用 Modbus TCP 或网关。
- OPC UA 采用轮询读取，尚未使用订阅（monitored items）。
- 单节点、无冗余；访问控制为单一操作员令牌，无角色划分。
- PCA 模型为静态模型，对大滞后过程的归因精度低于动态模型。
- 预测报警基于趋势外推：可预警逐渐发展的工况，无法预警突发故障。

## 许可证

MIT — 见 [LICENSE.md](LICENSE.md)。联系人：Anton Sergeev · kavery@mail.ru
