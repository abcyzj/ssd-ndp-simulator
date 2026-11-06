# P09 无内容 Layout

日期：2026-11-06（Asia/Shanghai）。需求依据为 [PRD FR06/09](prd-v0.4.5.md)、[P01 配置权威](contract-v0.4.5.md#6-配置权威和正式调度冻结)及[实施计划 P09](implementation-plan-v0.4.5.md)。**P09 已完成；P10～P21 未开始，全部新 AC 未验收。** 直接依赖 P01 已完成；本轮重新核对其设计 manifest 和全部登记产物 hash。当前可运行交付仍为 V0.4。

## 实现与用法

[Layout 接口](../src/layout/layout.h)及[实现](../src/layout/layout.cc)构成独立静态库 `ndpsim_layout`。调用方提供经 P02 校验的 `capture::Object` 目录和 `capture::Access`，只使用对象 ID、version、逻辑大小和访问范围；没有 ContentStore、内容文件路径、真实 Bytes、功能算子、设备或模拟时间依赖。不修改旧 Layout；旧路径在迁移期保留，删除属于 P20。

配置为 [ndpsim-layout/1 schema](../schemas/ndpsim-layout-v1.schema.json)，[图布局样例](../configs/layouts/graph-example.json)对应 `vectors/queries/graph` 三个只读对象。对象大小必须由实际 IR 决定，样例只是小型元数据实例，不是 SIFT1M 正式布局。字段全部显式填写，无默认覆盖：

- 单一 namespace：名称、`capacity_sectors`（sector 数量，不是末 LBA）、`sector_bytes=512`、`page_bytes`。page 必须是至少512B的二次幂；与设备真实几何的交叉核验由 P10 完成。
- `alignment_bytes`：extent 物理起点的二次幂对齐，可设1允许紧凑布局。访问本身可以非对齐，extent 长度无需补齐。
- 每个 `(object, version)` 恰有一项，extent 保存逻辑起点、物理**字节**起点和长度。逻辑 extent 经排序后必须完整且无重叠覆盖 `[0,size)`；零大小对象使用空列表。禁止未知/缺失/重复对象、逻辑缺口、物理字节重叠、越界和 uint64 加乘溢出。

Layout 逻辑大小与文件格式解耦，不推测 fvecs 头或 adjacency degree。不同对象可占用同一 sector 的互不重叠字节，只读请求会合并该 sector；不允许字节别名。LBA 是 namespace 内相对 sector 地址，partition 实际适用范围仍需 P10 核验。

文件引用相对**声明该引用的文件**解析，调用方必须传绝对声明文件路径；不以 cwd 寻找替代文件。加载器保存实际 canonical 路径、完整原始文件字节、SHA256 和完整有效 JSON，供后续 P14 写入运行快照。配置在加载时固定；两次运行之间改文件会产生新的 hash 和映射，无需重编译。没有实现运行中热更新。

```cpp
ndpsim::layout::Layout layout("layouts/graph.json", absolute_experiment_file, objects);
auto plan = layout.resolve(stage.accesses);
// source_path/source_bytes/source_sha256/effective_json 由运行入口保存。
```

[Python 生成器](../python/ndpsim/layout.py)提供 `packed_layout(manifest['objects'], capacity_sectors=..., page_bytes=4096, alignment_bytes=512, start_lba=0)`，按输入对象顺序分别对齐、紧凑排列，返回配置 dict。它只接受对象元数据，不打开原始数据，不自动构建、采集或运行仿真；独立拟合工具和旧 Python 布局代码没有新增依赖。正式用户 CLI 的配置读取/快照接线属于 P14。

## 读取计划及计量

`resolve`先验证所有访问范围（包括空访问 offset≤size），再将逻辑范围与物理 extent 相交，取整至 sector。相同 sector 在**同一次 resolve**中只读一次；相邻 sector 在同一 page 内合并成请求，page 边界强制拆分，不跨调用隐式缓存或预取。请求顺序按 LBA 升序；该计划仅在调用方依赖就绪后提交，本层不接触后端。

每个请求保留 access 序号、对象逻辑偏移、请求内字节偏移和有效长度的切片，使离散 extent、重复访问和共享 sector 的归属可独立核对。`logical_bytes`按访问重数求和；`sector_bytes`为唯一 sector 覆盖字节。它们均不等于 NAND、PCIe 或应用输出字节，也不在本层计费设备/链路。空访问无请求，但不绕过对象/version/范围检查。

文件最大16MiB、JSON嵌套最大32、对象10万、extent20万、单次访问10万、sector片段100万，均可由显式 `Limits` 缩小或调整；超限拒绝，不截断计划。片段上限按重复访问的切片数计，强于只限制唯一sector数。访问定位使用有序 extent 的二分查找；计划元数据受片段预算约束，不按对象/payload大小申请占位内存。预算是元数据条目/编码文件预算，不是硬 RSS 沙箱。

## 实际验证与证据

证据保存在 `results/v0.4.5/p09/run1/`：commands/cwd/退出码、配置/构建/原始测试日志、依赖核验、逐用例元数据输入与输出、迁移审计、链接/符号、源码/构建/子模块身份及产物 hash。`dev1/`保留初轮开发构建和测试日志。

- 根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure`：**105/105** 通过。唯一新增根 CTest 为 `layout_metadata_v045`。
- 最终[Layout 测试](../tests/test_v045_layout.py) **11/11**：普通读、跨sector/page、非对齐、空访问/零对象、重复读/共享sector、乱序/离散extent、60组固定种子随机访问；独立oracle逐逻辑字节枚举物理位置、核对全部切片与sector集合，另有1026B逻辑→2048B sector、请求`(7,1)/(8,3)`的手算断言。
- 负例覆盖未知格式/字段、重复JSON键、类型/数值非法、缺失/重复/版本失配对象、逻辑缺口/重叠、物理重叠、对齐、容量、加乘溢出、越界和各项预算；超大逻辑对象仅访问末字节/空范围，未分配等大缓冲；超大读按元数据预算拒绝。
- 外部cwd、Unicode/空格路径、引用相对声明文件及拒绝cwd回退；两次改配置改变LBA/hash，探针二进制hash相同；schema/图样例验证通过。
- 根入口 `build/p09-asan`，`NDPSIM_SANITIZE=ON` 定向编译并运行同一最终套件：**1/1 CTest（11/11子测试）**通过。P01设计 **11/11**、P00门控 **3/3**；迁移清单与实际根CTest命令核对通过。
- 实际链接及 `nm -C` 检查 Layout 探针无 content/功能算子/search/SST/MQSim 符号；`git diff --check`通过。没有修改依赖锁、MQSim/SST源码，没有提交或推送。

最终补充输入快照、无cwd回退断言并按仓库格式整理C++后，重新构建并执行根105项CTest及定向ASan；以最终源码身份记录通过。

## 限制与后续边界

本任务关闭 P09 实施出口，不关闭 AC07：MQSim运行时XML/partition/初态及设备几何交叉核验属于 P10，实际SSD/Controller传输与读映射属于 P12/P13，统一配置入口及最终快照落盘属于 P14。测试为独立无payload元数据夹具，未运行全量SIFT采集、正式六配置矩阵、MQSim对拍或CPU精度验收。旧模拟默认路径尚未迁移，不能把本库无ContentStore称为最终模拟器已去功能链接。

当前仅支持一个只读namespace、512B sector、无共享可变业务状态和物理字节别名。page来自显式配置，支持不等于已与任意MQSim配置验证；大规模前仍须本机资源预检。本层信任P02提供的对象目录语义身份，不重新验证digest/shape或证明业务正确性；hash只记录配置完整性，不提供恶意并发替换防护。物理布局修改不改变IR逻辑对象身份，映射/模型适用性仍须重新检查。本轮止于P09，不自动开始P10。
