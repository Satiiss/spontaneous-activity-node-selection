# HTTP 协议 v1

默认端口 8765。所有请求包含 `Authorization: Bearer TOKEN`，token 至少 16 字符。响应为 UTF-8 JSON。本版用于实验局域网 HTTP 通信，不提供传输加密。

## 开始

`POST /v1/start`

```json
{"request_id":"client-generated-unique-id","duration_s":600}
```

request_id 为 8–80 位字母、数字、下划线或连字符。返回任务元数据，包括 job_id、state、mode、duration_s、directory、mapping 和 sample_rate，不等待任务结束。

相同 request_id 与时长返回同一任务，历史任务也不会重新执行；同 ID 不同时长被拒绝。忙时拒绝不同请求。真实模式只允许 600 秒；mock 可用 0.1–600 秒测试。

## 查询

`GET /v1/status`

```json
{"protocol":1,"mode":"mock","hardware_fault":false,"job":null}
```

存在当前或最近任务时 job 是对象：

| 字段 | 含义 |
|---|---|
| job_id / request_id | 服务任务 ID / 客户端去重 ID |
| mode | mock 或 maxlab |
| state | starting、recording、stopping、finalizing、completed、stopped、failed、interrupted |
| elapsed_s / duration_s | 服务端计时 / 目标时长 |
| acquired_s / first_frame | 流覆盖时长 / 首个设备帧号 |
| sample_rate | 采样率，真实模式要求现场核验 |
| total_spikes | 完整已接收 Spike 数 |
| mapping | channel、electrode、x、y，坐标单位 μm |
| window_s | 放电率有效窗口，最长 5 秒 |
| rates | 每通道的 channel、electrode、x、y、hz |
| raster | [相对首帧的秒数, channel, amplitude] 列表 |
| raster_total / raster_sampled | 窗口内完整事件数 / 绘制是否抽样 |
| files / directory | 服务端文件路径 / 会话目录，不是下载链接 |
| error | 失败原因；收尾错误也报告失败 |

客户端约每 250 ms 发起一次不重叠的查询。网络错误时冻结最后确认状态，不得自行倒数到零并宣布成功。完成任务的最后图形摘要存于 session.json，支持服务重启后的查看。

## 停止

`POST /v1/stop`

```json
{"job_id":"server-issued-job-id"}
```

接收请求不等于停止完成。继续查询直到 stopped、completed 或 failed。不同 job_id 被拒绝。

## HTTP 状态

200：成功；400：非法参数、重复参数冲突或任务冲突；401：token 不匹配；404：端点不存在；500：内部异常。错误体 `{"error":"原因"}`。后台采集/保存失败通过 job.state 和 job.error 报告。

没有文件上传/下载、设备初始化、路由切换或刺激端点。

## 第二阶段分析（v1 增量扩展）

状态响应新增 `capabilities: ["candidate_analysis_v1"]` 和 `analysis`（未创建时为 null）。旧客户端可以忽略新增字段；新客户端连接旧服务时禁用分析按钮并提示更新 Linux。

`POST /v1/analyze` 请求：

```json
{"job_id":"server-issued-job-id","request_id":"client-unique-analysis-id"}
```

只允许当前 completed 录制。自动分析使用相同任务机制。相同请求或正在分析的录制返回现有任务；completed 结果直接返回缓存。取消、失败、中断后用新 request_id 重试。分析进行中拒绝开始新录制。

`POST /v1/analysis/stop` 请求：

```json
{"job_id":"server-issued-job-id","analysis_id":"server-issued-analysis-id"}
```

收到取消请求后继续查询直到 cancelled，取消不会修改原始录制。

analysis 字段包含 state（idle、queued、running、cancelling、cancelled、failed、interrupted、completed）、job_id、analysis_id、mode、stage、progress（0–100）、message、error、log、source_h5、directory 和 result。connections 阶段提供 pairs_done / pairs_total。进度日志保留最近 150 条事件。

completed 的 result 包含 spike_count、active_channels、mapping_count、burst_count、edge_count、candidate_count、threshold、candidates、electrodes、burst_preview、files、config 和 algorithm_sha256。candidates 顺序由算法排序确定，包含 channel、electrode、x_um、y_um、out_degree、in_degree、out_minus_in、burst_participation 等。文件路径均属于 Linux，不是 Windows 下载路径。threshold 可以为 null，候选列表可以为空。
