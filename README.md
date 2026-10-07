# 自发电活动观测与节点筛选

Windows 操作端与 Linux Maxwell 采集端放在同一个仓库，使用统一版本的 HTTP 协议。

当前已实现两个阶段：① 600 秒自发观测、实时图形与 H5 存档；② Linux 自动分析高出度候选，Windows 在同一窗口显示分析进度、候选表和电极空间位置。

**mock 软件链路和历史真实 H5 的候选分析已经过本地验证；真实 Maxwell 采集适配器尚未完成 Linux 现场验收。** 初始化、ActivityScan 和路由准备由现场 MaxLab 完成。当前没有刺激标定或猜拳操作。

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

完整录制保存成功后自动进入“② 高出度候选”。旧版本留下的完整录制可在第二页点击“分析已完成的录制”。提前停止、失败或中断的录制不进入候选分析。

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

## 第二阶段：高出度候选

Linux 子进程依次读取完整 Spike 文件、检测 Burst、计算首次激活与稳定先后关系、统计出度并保存候选。Windows 显示实际步骤进度及已计算的电极对数量；结果包含电极 ID、通道、出度、入度、出度－入度、Burst 参与率和空间位置。选择表格行会高亮对应电极。

分析沿用原 `spont_burst_outdegree` 算法，参数在 `linux/analysis.yaml`：共同 Burst 数严格大于 30、延迟直方图主峰计数与非零 bin 计数中位数的比值至少 20、绝对延迟严格大于 5 ms；默认按有出度电极的第 90 百分位筛选。阈值和候选数量来自本次数据，不固定为 33 或三个电极。候选表示自发活动中的稳定先后关系，尚未经过刺激验证。

mock 分析 `spikes.h5`，真实模式分析官方 `native*.h5`，坐标使用该 H5 的电极映射。结果保存在当前录制目录的 `analysis/<分析ID>/`：`result.json`、四张 CSV、参数快照、录制清单和进程日志。原始记录保留。无候选也是有效结果。

断开 Windows 不影响分析；重新连接恢复同一结果。取消或失败后可重新分析。Linux 服务重启会将进行中的分析标记为中断，不自动重复运行。分析期间不能启动下一次录制。可用 `--no-auto-analysis` 关闭自动触发，用 `--analysis-config PATH` 指定参数文件。

更新已有 Linux 部署时，先在服务终端按 Ctrl+C，然后：

```bash
cd /home/maxwell/Software/Hzl/spontaneous-activity-node-selection
git pull --ff-only
.venv/bin/python -m pip install -r linux/requirements.txt
.venv/bin/python -m linux.service --mode mock --host 0.0.0.0 --port 8765 \
  --token "$OBSERVER_TOKEN" --output "$PWD/data/mock-recordings"
```

保留原来的输出目录才能恢复上一次录制；token 环境变量须至少 16 字符。真实采集仍按现场部署文档和实际核验后的配置启动。

## 开发与验证

```bash
python -m pip install -r linux/requirements.txt
python -m unittest -v tests.test_service tests.test_analysis
```

Windows 需要 UI 测试时：

```powershell
.\.venv\Scripts\python.exe -m pip install -r windows\requirements.txt -r linux\requirements.txt
.\.venv\Scripts\python.exe -m tests.verify_ui
.\.venv\Scripts\python.exe -m tests.verify_analysis_ui
```

图形测试在隔离的本地 mock 服务上运行，结果写入被 Git 忽略的 `artifacts/validation/`。CI 配置包含 Linux/Windows 服务测试、Windows 离屏 UI 联调和 Linux C++ mock 构建；云端执行结果以 Actions 为准。

本项目从独立 `spontaneous_observer` 第一版整理而来，读取器来自已有的被动 Spike 组件。没有包含厂商 SDK 二进制或修改旧模拟软件。没有在本仓库新增开源许可证授权；厂商 SDK 的使用和分发遵循其原有许可。
