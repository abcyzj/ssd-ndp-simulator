# P13 图搜索 SSD 两放置

日期：2026-11-06（Asia/Shanghai）。依据 [PRD FR05/09](prd-v0.4.5.md)、[P01 分解及消息契约](contract-v0.4.5.md)与[实施计划 P13](implementation-plan-v0.4.5.md)。本任务交付双映射和定向证据；不关闭 AC06，不启动 P14，当前正式交付仍为 V0.4。

## 实现与服务边界

[图扩展 lowering](../src/extensions/graph/ssd.cc)独立于通用 DAG／成本公式／功能算子。图特定 C++ 映射放在 `src/extensions/graph/`，沿用 `src` 公共 include root；声明和训练配方仍在 `extensions/graph/`。没有修改 DAG 核心、SST Core 或 MQSim，也没有引入另一份后端源码。

- 同一 P04 IR/2 包可以选择 `host` 或 `controller`，DAG 对外仍保留原始稳定键、Work、features、输出及全部依赖。元数据 preflight 在创建设备前核验所有操作映射、Layout、模型与分解；未来访问只用于静态合法性及固定容量上界核验，不提前提交 I/O、credit 或计算服务。
- Host：INIT query、score_prepare vectors、adjacency_prepare 行访问通过普通 NVMe。`ExternalFlow` 校验真实 DMA 字节已到齐后消费 CQ，映射再经 phase1 通知输入就绪，随后提交整个 Host CPU 阶段。普通读取使用单独 FIFO，一次一个实际请求；它不添加 DAG 边，邻接分支仍可在读取与 CPU 之间重叠。JOIN 的引用访问不再重复读盘。
- Controller：query 的普通读和 Host init 完成后，实际 Open→query staging→OpenAck；score_prepare 只做完整一次 Host 命令准备，不把向量读回 Host。score_l2 的物理块经过 Execute→本地 NAND→input-write→compute-read→计算→result-write/read→Result。所有物理结果在 Host 到达后才发布原始 L2 token，原有 merge_append／其他后继仍受 DAG 门控。FINISH 的必要返回包含 Close/CloseAck，query worker／终态不早于会话排空。query 和 adjacency 仍走普通读；Controller 完整向量 D2H=0。
- 数据、wire、NVMe SQ/CQ、NAND、本地 staging 和 compute 各有自己的资源所有者。复用 P12 size-only 消息，不编码向量／IDs／score 数组，不分配业务 payload 等长占位缓冲。MQSim 保留预装载、FTL、CMT、NAND、PCIe、CQ 和测量窗口；预装载末端按设备页向上对齐。

复用 P11 的严格元数据配置／Profile 加载器，增加显式 SSD 模式及位置参数；默认内存模式不变。SSD 需求只使用 `ssd_host_return/1` 或 `ssd_controller_command/1`，不默用 resident Profile。捕获的 resident 逻辑语义可以复用；模型输入另作显式上下文投影：prepare 的 state_objects=0，由 MARK 的 Host state 独立计费；Host score_prepare 的 copied/decoded_bytes 为实际向量逻辑长度，Controller 两者为0；Controller merge_append 的 released_vectors=0。原始捕获计数不被回写，需求 trace 同时保留原始与投影来源。SSD Profile 必须覆盖投影后的真实特征和准备上下文，本任务的合成常数模型不是校准或采样证据。

## 分块与失败关闭

只允许 `graph-l2-items/1`：按 prepare 记录的访问顺序切完整 d 维向量，不跨语义 chunk、不断开维度。全包 preflight 得到单 item 的最大实际 sector 覆盖 S；固定物理上限为 `floor((block_bound-64)/(S+4+12))`，额外64B为命令 header，4B/item IDs scratch，12B/item结果。每块实际 Layout 覆盖及总 credit 再核验；容量还必须容纳 query credit 加一个最大块。块大小不取决于瞬时空闲量。

每物理 b 项重新生成 elements=bd、flops=3bd、read_bytes=4bd+4d、write/output_bytes=12b、candidate_checks/comparisons=0，并对实际子块调用 SSD Profile；从不把原调用 ns 除以块数。额外的 query-read 只进入物理 Work／缓冲账本。prepare、append、去重等不可分阶段保持完整调用边界。需要细分时拒绝逐调用实测表；模型逐块重新检查其声明特征域，不把完整块模型自动认证为适用于子块。无法容纳一个 item、未知映射／上下文／版本、Work 不符合规则、缺模型或超范围均非零退出且无成功 summary。

运行时存储／协议损坏、回调抛错或无法安全排空是实验致命错误，继承 P12 的失败边界；不提供设备复位／故障恢复。输入应保持不可变。仅支持图扩展只读语义、单 SSD、单 rank/thread、FIFO offered=0、workers/slots/admission=1/1/1。

## 入口与输出

[SST 模块](../src/sst/ssd_host.cc)使用 EventBridge 的 MQSim 模式，SST 是唯一生产时间权威。测试原生驱动只用于 ASan，不是生产第二时钟。

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
# SST 路径见 build/delivery/sst-paths.json
<SST> --add-lib-path=<绝对build目录> configs/graph_ssd_timing.py -- <run.json>
# 可重新生成的小图采集、双放置与负例：
ctest --test-dir build/delivery --output-on-failure -R graph_ssd_v045
```

`run.json` 格式 `ndpsim-graph-ssd-run/1`；包／报告／registry／Profile／target／workers／slots／admission／scratch／output 字段及 hash 规则沿用 [P11](memory-timing-v0.4.5.md)。新增 `placement`、`layout` 文件引用、`device` XML 引用和 `resources` 对象（严格正整数 `capacity_bytes`、`query_bound`、`block_bound`、`staging_bytes_per_ns`、`compute_slots`）。所有文件引用相对声明配置解析；scratch/output 必须新建。上下文必须与放置匹配，resources 不能覆盖设备 XML 的 PCIe 参数。统一安装 CLI、预算及用户报告属于 P14，没有在此实现。

输出流式 JSONL：配置／实际 Layout/XML hash 与有效值、原始逻辑 Work/features、每阶段服务来源、每物理 chunk 的需求与 extent、CPU/Controller 服务、普通／本地读、NAND transaction、协议 logical/payload/wire、实际 PCIe enqueue/start/arrive、buffer 访问／credit、DAG 阶段及请求窗口、排空 summary。SQ-fetch/SQE/CQE/data 分层；2B SQ-tail 与 CQ-head 在实际链路都标为 doorbell，提交和完成记录分别标注归属，不以 QD1 公式代替实际消息。`full_acceptance_passed` 始终 false，硬件仍为 `configured_unvalidated`。

## 实际检查与限制

最终证据位于 `results/v0.4.5/p13/run1/`，保存可重放编排、命令/cwd/退出码、原始日志、依赖 hash 复核、输入／源码／构建／产物身份和迁移审计。直接依赖 P04 run2、P08 run3、P12 run2 分别312、635、46个登记产物重新核验通过。

- 根配置 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、构建 `cmake --build build/delivery -j2`、完整 `ctest --test-dir build/delivery --output-on-failure`：**136/136 通过**（76.55秒）。
- [P13定向测试](../tests/test_v045_graph_ssd.py)：**7/7 通过**，实际从 `/tmp` 启动 SST，路径解析支持中文／空格。6节点×2维、11原始查询，原生采集后独立语义／Work校验，删除全部payload，再以同包执行319个逻辑阶段。Host 77个普通读、完整向量 D2H=16896 sector覆盖字节；Controller 44个query/adjacency普通读、33个本地读、完整向量 D2H=0。
- block_bound 从2160改为592B，无重编译；固定上限从3项变1项，本地块33→55，语义操作／Work／总flops／IDs和结果字节完全一致；额外物理query-read=8B×额外块数。每物理块 Work 按独立公式核对，没有将采集语义Work改成物理汇总。
- 独立核对 query／IDs／Result／ACK/Close 大小与 TLP取整；逐实际普通请求 SQE72/CQE16/SQ-fetch4/两次doorbell各2B及数据sector覆盖，NAND transaction与本地sector合计一致。buffer访问服务为 ceil(bytes/16)ns；credit未越4096B，依赖及所有Result到达门控、Close排空、task/pin/session/buffer归零。0ns/17ns模型改变模拟服务；同配置重复的全部非配置trace精确一致。
- 缺模型、hash失配、非法放置／上下文／配额／Layout、容量不足、未知图映射、不可分解规则、篡改L2 Work、试图分摊逐调用实测表均拒绝，无成功summary；重点负例实际错误信息与预期一致。
- `NDPSIM_SANITIZE=ON` 重新配置并构建 graph_ssd_probe、ndpsim_native、controller_timing_probe、memory_plan_probe，`ctest --test-dir build/delivery-asan --output-on-failure -R 'graph_ssd_v045|controller_timing|memory_plan_v045'`：**19/19 通过**。其中P13七项复用实际同一映射/后端，由测试驱动覆盖ASan；不是SST或上游完整泄漏审计，未新增泄漏豁免。
- P01设计11/11、P00门控3/3、迁移审计390文件/136CTest、`git diff --check`通过。`nm -C`/`otool -L`检查无ContentStore、Replay、search/operators或squared_l2符号；动态依赖仅SQLite及系统库。MQSim/SST commit、实际源码hash、相对锁定原生diff另存，本轮两个子模块均未改。

没有运行 SIFT1M 正式两点采集、SSD 准备上下文测量／拟合、P16 独立 MQSim 对拍或 P19 四个正式 SSD 账本；没有 CPU／真实硬件精度或下沉加速结论。完整 AC06 仍需 P15/P16/P19，旧 V0.4 通过不计本版完成。全包启动前做两遍有界逐查询扫描；成本模型与 registry 整体驻留，原会话内 chunk 去重目录仍随操作数增长。trace 磁盘预算和统一错误报告由 P14 接管；不宣称任意负载无限规模常量内存。当前普通 extent 上限65535 sectors、空本地读及全包无向量访问显式不支持。

开发日志与失败保留在 `dev1/`～`dev8/`：包括构建 include／重复声明、预装载页对齐、DAG 原始元数据与模型投影混用、测试 registry 身份及观测字段修正；没有将失败样本计为通过。没有提交、推送或启动下一任务。
