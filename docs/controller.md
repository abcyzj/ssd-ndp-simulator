# 盘控资源与生命周期

当前 V0.1 参数与行为说明；使用[统一运行入口](../README.md)，接口接线见[扩展指南](extensions.md)。历史验收见[归档](archive/v0.1/m3.3-controller-buffer.md)，模型边界见[限制](model-limitations.md)。

## 接口和资源归属

`src/model/controller.{h,cc}` 提供 `Controller::open(query, version, FP32 values)`、`score(query, vector selections)`、`close(query)`，均返回 Runtime 句柄。调用者可在 Open 完成前提交 Score，并提前提交 Close；设备命令等待 OpenAck，Close 等所有已提交 chunk 的实际回复到达后才发送。失败的语义批次不返回部分得分，独立 chunk 安全排空；Open 失败会传播到其后继。保留的内部句柄使调用者释放 Open 句柄后仍可推进；CloseAck 或失败清理后释放内部所有权。

- `BufferModel` 管理一次原子预留、严格 FIFO 等待和可分次释放的稳定 credit ID。单请求超过总容量立即 `CapacityExceeded`，暂满才等待；小请求不越过队首。重复/超额释放报错。记录每次增减的时间、query/task、生命周期、字节和占用，可独立重算峰值与守恒。`content::Code` 补齐冻结的 `CapacityExceeded`。
- `FifoResource` 用同一桥接事件实现单端口缓冲 FIFO 和可配置算术 slots；每 slot 非流水，算术时间为 `launch_ns + ceil(flops / flops_per_ns)`。零时长也异步执行，无墙钟延迟替代模拟耗时。
- MQSim 继续独占 NAND、cache/CMT、ONFI 和 PCIe 的计费。Controller 使用正式 `LocalReads`，消息通过共享 `PcieTransport` 和 `Conversation` 逐次到达校验，没有第二条带宽路径。
- 功能执行复用 FP32 输入、固定顺序 FP64 平方 L2，校验真实 Work 的 flops/output 与预留上界一致。功能结果在算术开始时私有计算；直到结果读出、ScoreResult 实际到达和 Runtime Publish 后，算法才能读取。

对象必须存活至其在途回调排空；单进程、单 SST rank/thread。研究账本与协议 ID 墓碑保存在实验宿主内存，不作为算法可读的设备内容。

## 确定性分块与前进空间

配置包含带单位的容量、staging B/ns、slots、launch ns、flops/ns、chunk 上限、max_inflight、维度、单向量 sector 覆盖上界及额外 scratch。参数来源默认 `configured_unvalidated`。

设 query 字节 `Q=4d`，每向量 sector 覆盖上界 `V`，额外 scratch `S`，总容量 `C`：

```text
n = min(chunk_limit, floor((C - Q - 64 - S) / (V + 12 + 4)))
W = 64 + S + n * (V + 12 + 4)
admission_limit = min(max_inflight, floor((C - W) / Q))
```

启动要求 query 加最小块可容纳、所有速率/slots/上限为正且整数计算无溢出；query/Score/结果 envelope 须满足原生 signed-int wire 上界。`V` 必须是 512 B 的正整数倍。接纳最多 `admission_limit` 个 query，即使它们都常驻也留下一个最大确定性块 `W`。这比只保留最小块更保守，避免固定大 chunk 因多个 query 常驻而永久等不到容量；不改变范围或数值模型。

每批输入在创建任何任务/消息/credit 前，完整校验 ID、版本、维度、范围、重复项与每向量 sector 上界。分块固定为每 `n` 项一块，最后一块取余数，绝不按瞬时空闲量改变。每块实际原子预留：合并后的输入 sector 覆盖量 + `12b` 结果 + `64+4b+S` 命令/scratch。超出声明上界的单向量明确拒绝，不无限等，也不静默切割向量。算法收到的是等待所有 chunk 后按输入顺序拼接的完整语义批次句柄。

接纳 FIFO 在主机保留尚未上传的 query；工作块也在主机等 credit，获准后才发送 Score、本地读取。因此设备没有无限免费命令接收队列。较保守的上界可能降低并发；实际 credit 使用 Layout 解析的覆盖量，而非功能有效字节。

## 生命周期与服务阶段

| 对象 | 预留/可用边界 | 释放边界 |
| --- | --- | --- |
| query | 上传之前预留 Q；QueryOpen 到达后执行一次 query-write；完成后 OpenAck | CloseAck 实际到达；Open 安全失败时 Error 实际到达 |
| input | Score 发送和本地读之前与结果/scratch 原子预留；Storage ServiceDone 后 input-write，之后 DeviceBuffer 内容才发布 | 算术最后使用完成；安全失败在相关 I/O/端口服务已排空的边界 |
| scratch/命令 | 与 input/result 同次预留，含完整 Score header/IDs 与配置额外 scratch | 同 input |
| result | 预留 packed ID+FP64 上界；算术完成后 result-write、result-read | ScoreResult 或错误回复实际到达后保守释放 |

缓冲端口默认单 FIFO，服务分别为 `ceil(bytes / staging_bytes_per_ns)`：query-write 一次；每块 input-write；compute-read（输入 sector 覆盖 + Q）；result-write；result-read。算术 rate 只含算术，不再包含这些缓冲访问。sector padding 是实际输入缓冲字节。scratch 只声明容量，本算子没有额外 scratch 数据访问，不能仅按容量伪造流量。返回主机目标内存默认无容量压力。

失败注入覆盖 Open 到达、query staging 完成、Score 到达、input staging 完成、功能执行、result-write 完成六个安全边界。失败发送一次有计费的 Error，停止本块后续服务；相关资源在安全边界释放，Error 到达后传播真实失败根因。早 Close 仍等同 query 其他 chunk 排空。LocalReads 在提交前拒绝的参数/能力错误也走块失败清理；提交后的 BackendFailure 保持终止实验语义，不在活跃 NAND 事务下伪造回滚或继续运行。

