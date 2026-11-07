# P10 MQSim 运行时配置

日期：2026-11-06（Asia/Shanghai）。依据 [PRD FR06](prd-v0.4.5.md)、[P01 配置权威](contract-v0.4.5.md#6-配置权威和正式调度冻结)及[实施计划 P10](implementation-plan-v0.4.5.md)。**P10 已完成；P11～P21 未开始，全部新 AC 未验收。** 直接依赖 P09 的 manifest 和710项登记产物 hash 已重新核对；本轮对 Layout 新增的映射末地址 getter 由当前测试重新验证。当前可运行交付仍为 V0.4。

## 实现和配置入口

[DeviceConfig](../src/backend/device_config.h)／[实现](../src/backend/device_config.cc)在运行时读取外部 MQSim XML；加载阶段不构造 SSD，不修改 MQSim 静态参数或模拟时间。XML 只解析一次，完整验证后保留不可变的字段、几何及预算，设备构造前一次性将全部支持字段写入 MQSim 参数。没有第二次生产 XML 解析或遗漏字段后继承静态默认值。

```cpp
ndpsim::DeviceConfig config("devices/ssd.xml", absolute_experiment_file, limits);
ndpsim::layout::Layout layout("layouts/graph.json", absolute_experiment_file, objects);
config.validate_layout(layout, preload_sectors);
ndpsim::OrdinaryDevice device(preload_sectors, config, layout);
// 在 SST/EventBridge 的既有初始化/唤醒/排空边界调用 start 和服务。
```

文件引用相对声明文件解析，声明文件路径必须绝对，不回退 cwd。`source_path/source_bytes/source_sha256/effective_json`分别提供实际 canonical 路径、原始字节、SHA256和完整有效快照。快照包含全部61个 XML 字段、全资源 partition IDs、实际namespace、page/raw大小、CMT entries、元数据估计、初态策略、预算和 `configured_unvalidated` 标签。原始文件在加载后移走仍可构造/运行，证明没有设备初始化时重新打开文件。最终用户 CLI 的配置关联、模型条件校验和快照写出由 P14 接线，本层不冒充已实现 CLI。

[OrdinaryDevice](../src/backend/ordinary_device.h)与[LifecycleBackend](../src/backend/lifecycle.cc)均接受同一 `DeviceConfig`。前者按有效几何生成全部 channel/chip/die/plane IDs，创建单个HIGH/TURNED_OFF partition；NVMe软件队列深度和设备队列深度同步，PCIe bandwidth/lanes显式取自 XML，保留 MQSim 原有报文取整和时序所有权。后者同样使用外部几何/partition，无Host流量、无预装载，初始化时间仍为0。

[外部XML样例](../configs/devices/)提供 native-small、ann-2g-v1、ann-16g-v1。P10 迁移期的命名 profile 和构建期 include shim 已在 P20 移除。生产配置显式引用 XML，修改文件无需重编译。设备夹具独立于生产目标。

## 支持子集、单位及失败关闭

[机器可读支持字段清单](../schemas/ndpsim-device-policy-v1.json)列出61个必填字段、固定值和范围；它描述本适配器支持的 MQSim XML 子集，不是任意 MQSim XML 的兼容承诺。完整树必须为 Execution→Host/Device→Flash；未知/重复/缺失字段或组、属性、嵌套scalar、混合内容、额外根、非整数、尾部垃圾、NUL、DTD/entity/comment/PI 均拒绝。允许首个 XML declaration；固定值区分大小写并使用清单中的字面量，包括原 MQSim 的 `Busrt`、`Metadat` 拼写。

支持 FLASH/NVME、MLC/ERASE、NVDDR2、PAGE_LEVEL、非Ideal Mapping、SHARED CMT、SIMPLE data-cache manager和PRIORITY_OUT_OF_ORDER/CWDP。partition始终关闭数据缓存，统计预处理关闭，GC/wear选项冻结为样例组合；不支持外部任意 flow/partition、SATA、TLC、HYBRID、ADVANCED、数据cache开启、统计预处理或多设备。修改不支持字段会拒绝，不静默忽略或转换。

| 可变参数 | 单位／主要约束 |
| --- | --- |
| PCIe lane bandwidth / lane count | GB/s/每lane，0.001～64；lanes 1～16；TLP/协议开销维持现后端规则 |
| NAND读/写/擦除/suspend与DRAM时延 | ns，正整数且≤10^9；SATA delay虽保留字段，在NVMe下不提供额外计费 |
| channel / chip / die / plane | 分别≤16/8/8/4，正二次幂；单partition覆盖所有ID |
| blocks / pages per block / page capacity | 分别64～65536块、16～256页、512～16384B，均二次幂；metadata不大于page |
| 队列 / fetch | 深度2～1024，fetch≥1且小于深度；不使用旧固定16代替XML |
| cache / CMT | bytes；data-cache容量含完整page；CMT≥128B且至少2个entry；row/burst二次幂且burst≤row |
| overprovisioning / seed | 比例0.05～0.30；seed 1～2147483600，保留MQSim构造时派生seed行为 |

乘加均检查uint64溢出；另限制raw sectors≤UINT32_MAX，避免MQSim内部unsigned-int几何乘法先溢出。namespace按MQSim每plane保留比例后的sector截断再求和，不能以raw容量替代。CMT entries按MQSim物理页地址宽度和sector bitmap的entry字节计算，不把配置bytes误作entries。

默认XML≤1MiB、深度≤4；设备分配前元数据预算4GiB、预装载≤100万page。元数据代理为`physical_pages*256 + blocks*4096 + CMT_bytes*64 + data_cache_bytes`，是保守接纳代理，不是硬RSS保证或真实硬件容量。调用方可以显式设置 `DeviceLimits`；没有全规模资源预检的许可含义。XML未知模式、预算、范围、队列和几何组合检查全部在SSD分配前完成；构造后再核对MQSim实际partition。

## Layout、namespace 与预装载

P09新增 `mapped_end_sectors()`，只反映extent覆盖的最高sector末端，不改变逻辑对象身份或旧读取计划。Layout namespace容量须不超过设备可用partition；page bytes必须与XML一致。预装载是显式的 `[0,preload_sectors)` 连续全page窗口，必须覆盖实际映射extent末端、对齐并处于设备namespace/初始化预算内；**不要求预装载整块声明namespace**，稀疏extent之间的空洞仍按连续窗口准备。无需真实内容文件。

保留 `PreloadWindow` 的逐page普通写、后台队列/CQ/doorbell完全排空、origin偏移和CMT留存；不重置后端时钟/累计统计，不在初始化完成前发测量读。大规模稀疏布局若连续窗口过大则拒绝，不自动跳过空洞或偷偷换初态。

核查修正了旧适配器的namespace计数混淆：MQSim用sector总数填入名称为`Get_max_logical_sector_address()`的字段，而旧ExternalFlow把该值当末LBA。新OrdinaryDevice使用真实partition的`end_lba=count-1`，构造后核对partition末端和Host-interface字段；不额外开放一个sector。raw范围内但超过可用namespace的请求现在在分配前拒绝。未改动MQSim或原生源码来掩盖该边界差异。

## 实际检查与证据

`results/v0.4.5/p10/run1/`保存依赖、根配置/构建/CTest、逐用例XML/请求/Layout及输出、迁移审计、源码/构建/依赖hash和最终总manifest。`asan1/`保存定向ASan，`oracle1/`保存独立原生导出/构建、六个XML、原生/fork/SST完整输出与精确比较。`dev1/`保留开发失败和修复前日志，不以新目录覆盖失败。

实际执行并通过：

- 根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure`：**106/106**。新增根CTest `device_config_v045`；P09 Layout及所有受影响旧断言仍通过。
- [运行时配置探针](../tests/device_config_probe.cc)配合[11项测试](../tests/test_v045_device_config.py)：独立ElementTree解释字段/几何/partition；20组固定种子几何；外部cwd/Unicode/空格路径；source/effective/hash核对；三份样例；无重编译改NAND/PCIe服务、geometry/page/queue/seed/CMT；加载后删除源文件；实际普通预装载/初态/CMT留存/read miss/排空和lifecycle 0ns。
- 分配前负例：未知/缺失/重复/非法XML、非有限/负值/溢出/尾随字符、未支持模式、整数宽度、非二次幂、非法OP/queue/页、元数据/XML/预装载预算、namespace超容量、Layout page失配和映射覆盖不足。错误输出核对`allocated=false`。
- `NDPSIM_SANITIZE=ON`重新配置/构建 `build/delivery-asan` 的 device_config/lifecycle/ordinary探针，定向CTest **4/4**（含相同11项配置子测试及普通读/小CMT）。未设置新的泄漏检测豁免，不宣称完整上游泄漏审计。
- 从 `deps.lock.json` 的 `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16` 独立 `git archive` 导出并构建未修改MQSim。共同夹具新增显式XML/声明文件输入；**原生使用其自身XML_deserialize应用原始文件，平台使用新typed activation**，并非两边共用参数赋值互证。native/fork循环与实际SST在QD1、QD32、small-CMT、NAND、PCIe、geometry/page/queue/seed六个配置全部严格一致；每配置实际SST重复2次，共12次。逐行比较CONFIG hash、预装载写和读的顺序/LBA/size/完成时间、INITIAL、CMT/CACHE、所有已预装载page的MAP和FINAL。
- P01设计 **11/11**、P00门控 **3/3**、迁移审计 **368文件/106根CTest**通过；生成header无XML、实际链接和无content/功能库符号、依赖源码身份及`git diff --check`核对通过。MQSim/SST源码和锁未修改，没有提交或推送。

开发期曾将MQSim的`Get_current_cmt_occupancy_for_stream()`误读为实时CMT占用；源码表明它返回统计预处理插入数，本夹具应为0。最终测试改为核对该值为0，并独立检查真实`Snapshot_cmt`非空、capacity、mapping、保留顺序及小CMT读miss；没有以删去CMT断言绕过失败。手工规则数量初轮断言也已改为按XML实际61字段核对，失败日志仍保留。

## 限制

本任务关闭P10实施出口，不关闭AC05/07：P16仍须提供正式独立对拍工具、请求清单/账本和最终后端接线证据；P14仍须完整配置入口、输出快照和模型条件绑定。本轮oracle核对是P10配置/共同行为检查，不是P16/P19、SSD下沉或硬件校准验收。未执行正式SIFT采集、六配置矩阵、CPU精度或真实SSD测量。

SST仍为生产时间权威；native/fork手动循环仅用于oracle探针，没有成为生产第二时钟。生产接口保留单rank/thread、单device lifetime；MQSim包含不可复用单例，不能在同一进程任意热切换设备。构造前验证可拒绝不合法输入，但不承诺任意构造异常后的全局恢复或恶意并发文件替换防护。实际RSS/初始化耗时受后端影响，全规模仍须新预检。

元数据普通设备路径不依赖业务payload；旧GraphHost/Controller/消息功能路径仍迁移共存，最终去功能链接和size-only传输分别属于P12/P13/P20。本轮止于P10，不自动开始P11。
