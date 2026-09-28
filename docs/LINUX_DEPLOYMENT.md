# Linux 现场部署

真实采集适配器尚未通过现场验收。先完成 README 的 mock 双机联调，再验证设备。

## 现场需提供或核对

- Linux 当前 IP、登录/操作方式和新项目目录。
- Python、MaxLab 版本，以及厂商 Python API 路径。
- 配套 C++ SDK 路径（include/、lib/）及编译器/CMake。
- 已完成初始化、ActivityScan、下载到设备的固定路由 CFG。
- 实际采样率、well、滤波方式。
- 保存目录、可用空间；确认没有其他程序同时操作官方录制与数据流。

这些信息是设备部署所需，不是建仓前置条件。真实配置在现场填写，不上传令牌、私钥或原始数据。

## 安装和配置

克隆整个仓库到 Linux 新目录，不覆盖旧服务。下文均在仓库根目录执行。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r linux/requirements.txt
cmake -S linux/reader -B linux/reader/build -DWITH_MAXLAB=ON \
  -DMAXLAB_ROOT=/path/to/maxlab_lib/maxlab
cmake --build linux/reader/build -j2
.venv/bin/python -m linux.export_mapping /absolute/path/current-routing.cfg /absolute/path/mapping.json
cp linux/hardware.example.json linux/hardware.json
```

SDK 不在仓库分发，使用现场配套版本。读取器使用 C++17；不指定 WITH_MAXLAB 时只构建 mock。映射导出只读 CFG，不访问设备，须人工确认 CFG 与设备当前固定路由一致。

编辑 linux/hardware.json，使用绝对路径填写 sample_rate、well、filter、routing_path、mapping_path、reader_path。核对后才将 prepared_fixed_routing、sample_rate_verified、exclusive_saving_confirmed 设为 true。示例 20000 Hz 不代表已经核实。

## 启动被动观测

```bash
PYTHONPATH=/path/to/api_utils .venv/bin/python -m linux.service --mode maxlab \
  --config linux/hardware.json --host 0.0.0.0 --port 8765 \
  --token YOUR_RANDOM_TOKEN_AT_LEAST_16_CHARACTERS \
  --output /absolute/path/recordings
```

PYTHONPATH 指向包含 maxlab Python 包的父目录。用 hostname -I 核对地址。Windows 填写相同端口与 token，仅在实验局域网开放端口。

本服务不调用初始化、路由下载、刺激电源或刺激命令。不要使用旧程序的 hardware/enable-hardware 启动方式；本仓库 maxlab 模式仅用于被动观测与官方录制。

## 验收和停止

开始后检查真实模式标识、recording 状态、图形和映射。零放电允许，设备帧必须继续到达。600 秒后先关闭官方录制与文件、关闭流并核验 H5，最后报告 completed。20 kHz、1024 通道、2 字节样本加余量的空间检查约需 32 GB。

任务目录保存 session.json、mapping.json、spikes.h5；真实模式还保存 native*.h5、routing.cfg、acquisition_config.json、reader.log。spikes.h5 不包含原始电压，官方文件才用于原始信号验收。

现场仍需核验 SDK 应答、连续流吞吐、路由与采样率、原始电压完整时长和十分钟长稳。当前 H5 可读性检查不能替代这些验收。流首帧可能晚于官方文件起点。

提前停止需等待 stopped，不能视作完整十分钟。关闭 Windows 不会停止录制。服务端 Ctrl+C 会请求停止并等待收尾；强制终止或断电无法保证设备端录制停止。

failed/interrupted 后先确认官方 Saving 与读取进程已停止，保留故障证据，用新的 output 目录重启。不要并行运行两个服务操作同一设备。
