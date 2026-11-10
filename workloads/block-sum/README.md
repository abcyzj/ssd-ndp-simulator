# 只读块求和扩展（P17）

这是一个完整、固定规模的教学扩展：真实原生执行／采集 → 独立功能与 Work 核对 → 新算子与 fork/join → 成本模型 → SSD Host／Controller 映射和比较。它只验证扩展接口，不是第二个精度负载，也不承诺下沉更快。

默认输入为 little endian u32 `[1,2,3,4,5,6,7,8]`。`left` 和 `right` 分别读取前后16B并产生8B u64（10和26）；`join` 依赖两者的 DATA 完成令牌，求得36。原生文件故意按 join、right、left 排列；唯一拓扑 rank 不构成 ORDER 边。模型每阶段需求为 `ceil(3 + 2×elements) ns`，对应11、11、7ns。两位置使用同一包、相同目标、双算力槽和 FIFO 单请求策略；join 在 Host。该 Profile 的 `fixture=true` 与占位测量 hash 明示手工教学参数，不能作为真实测量、拟合或精度证据。

## 复现

[快速上手第一课](../../docs/quickstart-v0.4.5.md#第一课--块求和)使用公共 `ndpsim capture → check → simulate → compare`。默认配置在 `capture.json`，可复制后修改八个 u32 值和设备路径。也可独立构建原生采集程序，只需要本目录与显式提供的 SDK。

```sh
cmake -S workloads/block-sum -B build/block-sum-native -DNDPSIM_SDK_ROOT="$PWD/sdk"
cmake --build build/block-sum-native -j2
ctest --test-dir build/delivery --output-on-failure -R '^(extension_v045|unified_cli_v045)$'
```

`block_sum_capture.py` 是 CLI 显式适配器。原生程序接受 `INPUT NEW_CAPTURE REGISTRY_SHA SOURCE_SHA`。Python oracle 独立重建期望数值并逐字段核验操作、Work、端口、访问、依赖、终态和输入身份。采集完成后删除临时二进制输入。模拟只读取元数据和已绑定的收据。公共比较报告是描述性输出，独立的流量、服务、依赖和排空断言由 P17 和 CLI 回归执行。

## 扩展落点

- `contract.h` 和 `operators.json`：同一公开算子契约的编译期声明与外部登记；封包时二者必须完全一致。8B固定输出大小规则通过公共 registry 接入。
- `native.cc`：真实数值实现及 SDK Recorder；不链接模拟器。
- `tutorial.py`：独立 Python 功能／Work oracle、封包、Profile和配置生成、独立流量/依赖核对。通用 `ndpsim check` 可检查结构；业务语义核对由此适配器完成。
- `run.h`：扩展自己的 lowering 回调；通用 DAG 按依赖调用。Host 使用普通 NVMe read，经数据 DMA 和 CQ 后才计算；Controller 注册 `example-sum/1` 协议，通过真实 local-read、buffer、compute、结果返回及 CloseAck。输入不回传 Host。命令和返回显式为48B/40B，返回逻辑8B；Open/ACK/Close为32B封套。PCIe由现有共享链路计费，不在公式重复收费。
- `sst.cc` 与公共包的 SST 入口脚本负责编译期模块接线。`ndpsim simulate` 按显式运行格式启动该模块，SST 提供模拟时间。
- `probe.cc`：仅用于 ASan 的测试事件驱动，不是生产第二时钟。

根工程仅新增 `add_subdirectory(workloads/block-sum)`；核心 DAG、Runtime、cost、Controller、Layout、SST Core和 MQSim 源码均不改。新增真实业务算子需要同时修改本目录的契约、原生实现、独立oracle、成本适用域和映射，不是仅改算子名称。

## 范围和限制

固定一个请求、两个16B只读分支、两个计算槽、关闭data-cache的单SSD；分支读经sector取整，各512B。Host普通请求2个、输入DMA1024B；Controller local请求2个、输入D2H=0、结果逻辑共16B。两分支可同时ready，但仍受设备、共享链路、buffer资源竞争。汇合完成还等待Controller会话排空。

现行公共 provenance 的 split 枚举仅有 tuning32/evaluation128/fixture，且 fixture 只允许 synthetic-fixture。为了真实标记 native-capture，本例使用 tuning32 标签和明确的 workload=block-sum、query_ids=[0]；此标签不意味着32查询或正式图搜索训练，绝不与图搜索数据混用。P17保留公共schema，没有扩大split格式。

教学Profile、SSD/PCIe/Controller均 configured_unvalidated；没有CPU校准、吞吐精度或硬件收益结论。示例元数据规模极小，子进程有30/60秒超时，读取元数据限制16MiB；不承诺通用大规模输入或恶意文件沙箱。完整发布验收见 [V0.4.5 发布记录](../../docs/releases/v0.4.5.md)。
