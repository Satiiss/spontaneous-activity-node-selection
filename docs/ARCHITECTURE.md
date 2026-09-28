# 架构与阶段边界

```text
Windows Qt 客户端
   │ HTTP JSON / 8765 / token
   ▼
Linux 任务服务 ───── session.json、mapping.json、spikes.h5
   │
   ├── mock：合成 Spike + 合成坐标（明确标记）
   │
   └── maxlab：官方 Saving → native*.h5
              被动 C++ 读取器 → 连续帧/Spike → 5 秒图形摘要
```

`shared/protocol.py` 只包含双端常量，没有 Qt、numpy 或设备依赖。UI 不导入服务端或厂商模块；服务端不依赖 Qt。`linux` 命名空间表示部署归属，其 mock 模式也能在 Windows 运行。

## 第一阶段

1. 在 Linux MaxLab 完成初始化、ActivityScan、电极选择和固定路由准备。
2. Windows 请求 600 秒自发录制；Linux 分配唯一任务并返回。
3. Linux 保存数据并提供状态/图形摘要；Windows 周期查询。
4. Linux 收尾后报告 completed、stopped 或 failed。

服务端使用单调时钟计时，真实模式起点为 Saving.start_recording 返回之后。实时流起点为第一批设备帧，可能略晚于官方文件起点。流覆盖时长、服务端计时和文件最后一个 Spike 时间不是同一个量。

当前对收到的帧进行连续性检查。官方 H5 检查可读性、采样率、通道/电极映射和 Spike 数据集；未实现原始电压完整时长的全面校验，必须现场验收。

## 故障与保存

启动请求按 request_id 去重。停止请求必须匹配当前 job_id。客户端断开不改变任务状态，不允许暂停连续录制。

session.json 定期以临时文件替换方式保存。服务重启将活动任务标记 interrupted，不会擅自恢复。真实任务出错或中断会锁定新任务，须现场确认官方录制与读取进程已停止，保留故障记录、使用新的输出目录重启。单台设备只运行一个采集服务。

每次记录保留模式、采样率、映射和请求 ID；真实模式额外保留路由文件及 SHA-256。正式使用仍需人工确认 CFG 是当前设备实际路由，服务未实现独立的设备路由发现。

## 下一阶段

现场十分钟录制验收后，继续接完整文件的高出度候选分析、结果导出和候选测试。本仓库当前不包含刺激调用、自动标定或猜拳，mock 结果不能声称实验有效。
