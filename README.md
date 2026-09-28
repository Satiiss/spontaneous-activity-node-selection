# 自发电活动观测与节点筛选

Windows 操作端与 Linux Maxwell 采集端放在同一个仓库，使用统一版本的 HTTP 协议。

当前已实现第一阶段：600 秒自发观测任务、倒计时、实时放电栅格图、电极活动热力图、全部映射通道放电率、停止保存、断线恢复与 H5 事件存档。

**mock 软件链路已经过本地验证；真实 Maxwell 采集适配器尚未完成 Linux 现场验收。** 初始化、ActivityScan 和路由准备由现场 MaxLab 完成。本阶段没有刺激操作。项目名称中的“节点筛选”是后续阶段，当前仓库尚未接入候选分析、刺激标定或猜拳。

## 目录

```text
windows/           Windows Qt 客户端、启动器和 UI 依赖
linux/             Linux 采集服务、真实/mock 数据源、示例配置
  reader/          被动 C++ Spike 读取器（硬件模式需要现场 SDK）
shared/            双端共用的协议版本、端点与任务状态
tests/             服务测试与 Windows/HTTP 联调测试
docs/              架构、协议、Linux 部署及验证说明
.github/workflows/ 硬件无关的自动检查
```

从仓库根目录执行下面的命令。建议使用 Python 3.12。运行时默认保存到 `data/recordings/`；数据、真实配置、访问令牌及厂商 SDK 不提交 Git。

## Windows：连接现场服务

```powershell
git clone https://github.com/Satiiss/spontaneous-activity-node-selection.git
cd spontaneous-activity-node-selection
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r windows\requirements.txt
.\.venv\Scripts\python.exe -m windows.app
```

之后可双击 `windows/start.bat`。填写 Linux 当前 IP 与端口 `8765`，输入服务端配置的 token，点击“连接服务”→“开始 10 分钟录制”。Linux 上用 `hostname -I` 核对地址。

仅本机 mock 自检时，再安装服务依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r linux\requirements.txt
```

先打开 `windows/start_mock_service.bat`，保持它运行；再打开 `windows/start_demo.bat`。窗口会明确显示 `MOCK · 模拟采集`。启动器优先使用仓库自己的 `.venv`，也允许用 `OBSERVER_UI_PYTHON`、`OBSERVER_SERVICE_PYTHON` 指定其他已安装依赖的 Python。没有旧模拟软件的运行时依赖。

## Linux：无硬件联调

```bash
git clone https://github.com/Satiiss/spontaneous-activity-node-selection.git
cd spontaneous-activity-node-selection
python3 -m venv .venv
.venv/bin/python -m pip install -r linux/requirements.txt
.venv/bin/python -m linux.service --mode mock --host 0.0.0.0 --port 8765 \
  --token YOUR_RANDOM_TOKEN_AT_LEAST_16_CHARACTERS --output ./data/recordings
```

双端版本应来自相同提交。真实模式详见 [Linux 部署](docs/LINUX_DEPLOYMENT.md)，需要现场配套 MaxLab Python/C++ SDK、实际固定路由和人工核验后的采集配置。

## 录制和显示规则

- Linux 负责计时及任务生命周期，Windows 不独立推算完成状态。
- 最近 5 秒显示 Spike 栅格及放电率，开始不足 5 秒时按实际流覆盖窗口计算。栅格最多显示 8000 个采样事件，H5、累计计数与放电率不抽样。
- 全部映射通道保留，包括零放电通道。热力图使用实际映射坐标；mock 坐标为合成数据。
- 到时先停止采集、完成保存及核验，再报告完成。提前停止为 `stopped`，不能当作完整十分钟。
- 关闭客户端或网络断开不会停止服务端录制，重连可以查询原任务。
- `spikes.h5` 是收到的 Spike 事件存档，不包含原始电压。真实模式额外使用官方 Saving 保存 `native*.h5`。

详细说明：[架构](docs/ARCHITECTURE.md) · [协议](docs/PROTOCOL.md) · [验证范围](docs/VALIDATION.md)。

## 开发与验证

```bash
python -m pip install -r linux/requirements.txt
python -m unittest -v tests.test_service
```

Windows 需要 UI 测试时：

```powershell
.\.venv\Scripts\python.exe -m pip install -r windows\requirements.txt -r linux\requirements.txt
.\.venv\Scripts\python.exe -m tests.verify_ui
```

图形测试在隔离的本地 mock 服务上运行，结果写入被 Git 忽略的 `artifacts/validation/`。CI 配置包含 Linux/Windows 服务测试、Windows 离屏 UI 联调和 Linux C++ mock 构建；云端执行结果以 Actions 为准。

本项目从独立 `spontaneous_observer` 第一版整理而来，读取器来自已有的被动 Spike 组件。没有包含厂商 SDK 二进制或修改旧模拟软件。没有在本仓库新增开源许可证授权；厂商 SDK 的使用和分发遵循其原有许可。
