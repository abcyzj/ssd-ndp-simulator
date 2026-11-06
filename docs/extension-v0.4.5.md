# P17 教学扩展全链路

日期：2026-11-07（Asia/Shanghai）。依据 [PRD FR04/05/07/12、AC09](prd-v0.4.5.md)、[实施计划 P17](implementation-plan-v0.4.5.md)。**P17 已完成**，实现及教学夹具出口见下；没有启动 P18，不计正式精度或最终12项AC验收。当前正式可运行交付仍为 V0.4。

## 实现

[可独立复现教程](../examples/extension/README.md)展示固定8项u32的真实块求和。独立C++原生程序只依赖SDK，执行两个16B分支和一个u64汇合，SDK按逆拓扑文件顺序采集。Python独立整数oracle检查全部操作、输出digest、Work、访问、依赖与终态，再生成语义receipt；通用封包／校验不认识块求和语义。原生默认输出10、26、36；还覆盖包含UINT32_MAX的输入。

扩展契约、外部registry、原生实现、纯元数据lowering、SST模块、协议大小规则、成本配置生成、独立核对器和测试均位于 `examples/extension`。根CMake只增加编译期扩展登记；没有修改通用DAG、Runtime、Cost、Layout、Controller、SST Core或MQSim。迁移清单逐文件登记新增源码／教程和CTest。

模拟沿用同一个已独立验证的新IR包，只切 placement。两分支在相同模拟时刻ready，汇合只能在两结果返回后放行；没有按rank或文件顺序补ORDER边。Host经普通NVMe数据DMA/CQ再计算；Controller经新注册的size-only协议、local-read、buffer、compute和结果返回，CloseAck也纳入汇合返回门控，随后DAG回收所有令牌。没有读取payload、真实sum、等大占位数组或另设生产时钟。

独立成本为教学线性公式 `3+2×elements ns`，左右各11ns、汇合7ns；Host/Controller均两个计算槽，join在Host。Profile `fixture=true`，来源占位hash明确只表示假设，不冒充Measurement/拟合；服务、排队、链路和设备各由原有所有者计费。独立账本检查逻辑输入32B、每分支512B sector、Host普通请求2个及输入DMA1024B、Controller local请求2个及输入D2H=0；结果每分支逻辑8B，命令48B/返回40B、控制32B，wire各加一个28B TLP开销。

## 验证和证据

最终证据为 `results/v0.4.5/p17/run2/`，包含可重放编排、命令/cwd/退出码、直接依赖gate、源码／构建／二进制及产物hash、隔离源码／SDK／安装包、实际双映射trace和独立比较。P02 closeout1、P05 run4、P08 run3、P12 run2、P14 run1 的manifest及共2893项登记产物hash重新核验通过；仅证明依赖证据完整性。

- 根配置 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、构建 `cmake --build build/delivery -j2` 均退出0；完整 `ctest --test-dir build/delivery --output-on-failure` **140/140通过**（116.76秒）。P17注册测试内 **5/5方法**通过，包含真实SST双映射、非默认输入/重复确定性、独立oracle的输入和Work篡改、非法放置/Profile/语义hash拒绝，以及成本变更使实际服务和时间改变。
- 独立隔离目录只复制本例与SDK，从 `/tmp` 配置/编译原生成功，`otool -L`只含libc++/libSystem；不携带模拟src或第三方模拟源码。只复制Python包并安装到独立target后，从外部cwd、Unicode/空格路径实际复现完整教程及 `python -m ndpsim check`，均退出0。
- 教程默认输出10/26/36，删除原始payload后，Host测量窗口154134ns、Controller153872ns；这两个数仅是教学参数下的预测，不能推断真实硬件收益。请求、流量、依赖和全部资源归零均由独立断言核对。
- `cmake -S . -B build/delivery-asan -DBUILD_TESTING=ON -DNDPSIM_SANITIZE=ON`、构建 `block_sum_probe block_sum_native` 后，定向 `ctest --test-dir build/delivery-asan --output-on-failure -R '^extension_v045$'` **1/1通过（同5方法，2.10秒）**。实际映射probe含ASan；不是SST全栈或SDK/原生程序全量ASan审计，没有新增泄漏豁免。
- P01设计 **11/11**、P00门控 **3/3**；迁移审计 **415文件/140CTest**。核心/后端源码未改、依赖commit/实际源码、无功能符号链接、文档链接及 `git diff --check` 均通过，身份和产物hash见manifest。

开发失败保留于 `dev1/`、`run1/`：首次构建缺少显式JSON头路径；最初使用公共schema未支持的teaching/fixture来源组合、重复rank，随后改为native-capture支持的split及连续唯一rank；SST参数Unicode转义与重复JSON解码修正；测量回调误接管预装载完成、Close晚于DAG全局排空均已修正。未修改核心约束或放宽断言。首轮完整根CTest为138/140，既有CLI/Validator两项因沙箱禁止只读sysctl资源预检失败，保留日志并在允许读取系统信息后复验。

## 限制

仅固定单请求、两个只读分支的教学夹具。没有SIFT正式采集／精度、P19正式SSD账本、真实硬件校准或第二负载精度承诺；所有结果保留 `full_acceptance_passed=false` 与 `configured_unvalidated=true`。AC09的可复现实施证据已提供，最终12项AC仍须P21按清理后身份复核。

现有provenance的split枚举仍受tuning32/evaluation128/fixture约束，fixture只能用于synthetic-fixture。本例为了如实保留native-capture，使用tuning32标签，且以 `workload=block-sum`、`query_ids=[0]` 隔离；这不是32查询或图搜索训练证据，不得进入正式训练／评价。没有为了教学例修改公共schema。

统一CLI可执行通用结构check；扩展的语义核对及自定义双映射通过显式tutorial/SST模块执行，没有为现有 `ndpsim simulate` 增加动态插件或旧命令兼容层。元数据有16MiB检查、子进程30/60秒超时，仅承诺此小例；不是通用大规模或恶意输入沙箱。本次没有执行P18/P19或最终总验收。无Git提交、推送或依赖更新。
