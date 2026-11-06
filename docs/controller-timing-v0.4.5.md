# P12 元数据传输与 Controller 资源

日期：2026-11-06（Asia/Shanghai）。依据 [PRD FR01/05/07](prd-v0.4.5.md)、[P01 消息与资源契约](contract-v0.4.5.md#5-时间令牌消息和资源)及[实施计划 P12](implementation-plan-v0.4.5.md)。P12 只关闭资源底座与定向验证，AC03/06 尚待后续完整链路；当前可运行交付仍为 V0.4。

## 实现

- [消息描述与传输](../src/timing/messages.h)／[实现](../src/timing/messages.cc)：只保存协议、kind、query/task/token/version/chunk/count/dimension/status 等固定字段。`MessageRules` 注册纯元数据大小规则；内置 `graph-readonly/1` 保持 Open=64+4d、Execute=64+4b、Result=56+12b、ACK/Close/Error=48B，useful logical 分别为4d/4b/12b/0。未知协议、重复注册、方向/kind、状态、身份或长度溢出拒绝。业务 ID 顺序和语义由采集侧校验，线上计费不会因省去业务数组而减少。
- `MetadataPcie` 把显式 envelope 长度写入 MQSim `PLATFORM` 报文，`Payload=nullptr`，不 encode/decode 业务数组、不分配等长占位数据。保留共享 PCIe 双向 FIFO、TLP 取整和原生完成事件；记录 logical/payload/wire、提交/到达时间、方向和任务。到达后经 EventBridge phase1 回调，零时长亦异步；Endpoint 当前为0服务，不重复收链路带宽。
- [本地读](../src/timing/local_reads.h)：消费 P09 `layout::Plan` 与 SDK Key，复用现有 MQSim `Validate/Submit/Inspect/Take_local_*` 服务和逐 transaction 核验，完全不使用 ContentStore、content Handle 或业务 payload。全计划在首个请求前验证；合并访问的 logical_bytes 可以大于物理 sector_bytes，不能混淆两层计数。父请求等全部本地读完成及安全清理后才回调；元数据/时间/transaction 不符为致命实验错误，不伪造成功或释放在途 I/O。
- [Controller](../src/timing/controller.h)／[实现](../src/timing/controller.cc)：接收显式物理块、输出描述、scratch 容量/访问 Work 和服务需求。查询上传与 staging、原子块 credit、命令到达、本地读、input-write、compute-read、compute slots/FIFO、result-write/read、结果到达和 CloseAck 构成真实服务链。输入/scratch 在最后计算使用后 phase1 部分释放；结果保留到返回到达，query 保留至 CloseAck。BufferModel 新增受 owner/delivered/unpinned 检查的 `release_part`，释放经 phase1 后才唤醒等待者。
- `execute_profile` 调用 P05 `Profile::demand`，核对稳定 stage key、Work/features 和 Controller 位置；Profile 自行检查 target/context/范围/绑定。新增只读 `Profile::source_sha256()`，计算记录带实际已验证 Profile hash 与阶段来源。`execute` 仍接受显式需求，供映射或明确标注的实验假设使用，不猜算术公式或按比例拆 Work。buffer 访问另记字节/服务 ns/任务键，排队由资源记录给出。
- Runtime 输出只在输入、计算和必要返回全完成后 Visible；复用 P07 四 phase 与 owner/pin 机制。`fail` 停止同会话未发服务，撤销排队 credit；已发 I/O、端口、计算和传输保留 credit 至实际回调，已接收命令经实际 Error/Result 返回后才发布失败，后继不发服务。Close 等所有块排空，不依赖失败块的成功令牌。已关闭 query、token 和协议任务不能复用，退休 ID 采用区间压缩。

新模块 `ndpsim_controller_timing` 只链接 metadata Runtime/Layout/Cost、SDK 与 MQSim。旧功能 Controller/transport 暂存用于迁移期核对，未成为本模块依赖；P20 才删除旧路径。本轮不改 MQSim/SST、不提交或推送 Git。

## 实际检查与证据

最终证据位于 `results/v0.4.5/p12/run2/`，含命令/cwd/退出码、stdout/stderr、依赖 manifest 与逐产物 hash 核对、源码/构建/依赖身份、链接符号和迁移审计。直接依赖 P05 run4、P07 run2、P09 run1、P10 run1 的登记产物均重新核对。

- 根配置 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、构建 `cmake --build build/delivery -j2`，完整 `ctest --test-dir build/delivery --output-on-failure`：135/135。
- `ctest --test-dir build/delivery --output-on-failure -R controller_timing`：27/27，其中10项实际 SST。独立手算 Open192/Execute76/Result92/control48B；PCIe wire248/76B、每lane62/19B、双向 FIFO、lane rates1/2/1000000、0B异步及100MB虚拟消息。Controller 夹具无任何业务输入文件，本地逻辑1080B/sector2048B；buffer五段服务8/128/136/3/3ns、计算17ns，固定初态下测量完成154162ns。真实链路竞争、模型服务变化分别增加输入1248ns、计算100ns、返回1245ns；零计算减17ns。每项精确匹配，无百分比容差，后继 start 等于 Result 实际到达。
- 7个服务边界故障、输入/计算/返回在途故障、满容量 FIFO 等待和排队撤销；失败不发后继、Close排空、query/block/credit/task/owner/pin/本地请求/PCIe全部归零，完整向量 D2H=0。还覆盖容量不足、块容量算术溢出、最后一个非法extent、输出大小失配、重复chunk/会话、非法消息身份/状态/大小、模型key/Work/context/target失配、部分释放的pin/越界负例。
- ASan：重新配置 `build/delivery-asan -DNDPSIM_SANITIZE=ON`，构建 controller_timing/timing_runtime/cost_model 探针，定向 CTest 19/19。与根测试相同的17项 Controller 原生断言及 Runtime/Cost 检查，不新增泄漏豁免；不是SST或上游完整泄漏审计。
- P01设计11项、P00门控3项、384文件/135根CTest迁移审计、无content/operators/search/旧runtime/L2符号、依赖身份及 `git diff --check` 均通过。

`dev1/` 保留开发构建日志，`run1/` 保留首次完整回归的128/129结果。失败来自P11测试临时目录 `p11 memory ...` 的空格被SST模型参数再次切分，之前以无空格证据目录运行未暴露。修正测试传入带双引号的模型参数，继续使用原有带空格目录及全部原断言；修正后的定向检查与最终根回归重新执行，不把首轮计通过。

## 使用边界与限制

本接口是资源层，不是用户CLI或图搜索SSD映射。调用方须先完成依赖门控，再提交块；P13负责从新包/图扩展生成两放置计划、容量分块和可分解 Work，不在P12提前实现。query_bound/block_bound/admission 是显式资源上界，启动时要求全部准入query之外至少容纳一个最大块；准入已满立即拒绝，由上层调度等待，绝不按瞬时容量改变语义chunk。空本地读取计划目前拒绝，零服务计算和0B消息合法；不承诺任意无存储算子的Controller映射。

调用方须独占本地完成队列、定期 poll，待设备预装载/测量窗口就绪后提交，所有adapter/Runtime/资源所有者必须存活到排空。受控会话故障用 Controller::fail；绕过该所有者直接对其token调用Runtime::fail不属于此复合adapter的故障接口。后端注入后抛错、事务元数据损坏、回调抛错或无法安全排空直接非零终止，不提供设备复位/故障恢复。历史记录可关闭留存或使用sink；活跃会话内的chunk/task去重目录仍随该会话操作数增长，不宣称任意负载无限规模常量内存。

本轮使用小型固定后端夹具，没有执行正式SIFT采集/CPU精度、P16独立MQSim对拍工具、P19完整SSD账本或任何新AC总验收；SSD/PCIe/Controller保持configured_unvalidated。P12范围到此，P13～P21均未开始。
