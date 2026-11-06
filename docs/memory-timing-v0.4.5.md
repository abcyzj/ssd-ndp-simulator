# P11 内存 timing-only 接线

日期：2026-11-06（Asia/Shanghai）。依据 [PRD FR01/07/10](prd-v0.4.5.md)、[P01契约](contract-v0.4.5.md)及[实施计划P11](implementation-plan-v0.4.5.md)。直接依赖 P04 run2、P05 run4、P08 run3 的 manifest 与全部登记产物在本轮重新校验。P11仅交付内存接线与小夹具证据，不关闭 AC03/04；P12～P21 未开始，当前已验收交付仍为 V0.4。

## 接口和计费

[Plan](../src/mapping/memory.h)／[实现](../src/mapping/memory.cc)加载新包、P02结构报告、registry、目标与P05 Profile。配置使用独立格式 `ndpsim-memory-run/1`；所有文件引用相对声明配置所在目录解析。输入不接受 vectors、queries、graph、truth、旧 engine 或 SSD 选项；没有 dataset 准备、真实搜索、内容加载或功能回放前置。`native-capture` 仍必须提供外部hash绑定的P04语义报告；synthetic fixture 必须显式允许。

每个已就绪stage用原始稳定键、算子语义版本、kind、context、Work及features调用 `Profile::demand`，位置为host。服务需求以ns交给同一HostCpu FIFO资源，每阶段只提交一次。输出仍是描述和依赖token，不分配逻辑长度的业务缓冲，不重新执行L2、排序或去重。resident的准备/复制等成本由对应Profile阶段承担，没有额外DRAM/PCIe/SSD收费。图配方的state/join零服务及唯一插入计费仍由P04语义证据和P06配方承担；本接线不把任意用户Profile认证为正确校准。

逐调用实测表binding由本次包provenance、package hash、配置target/context、workers/slots/admission构造，不能由用户另传一个假binding。表缺项、Work/身份/配额不符及模型缺失/超范围均失败关闭，无Host回退或常数补洞。模型目标不要求等于运行模拟器的宿主。

[内存Host](../src/sst/memory_host.cc)继承P08通用SST GraphHost，使用PlatformOnly事件桥，不创建OrdinaryDevice/LifecycleBackend，不调用MQSim存储服务。包级workers/admission复用P08，CPU slots由P07唯一资源所有者管理，三者不合并。请求为offered=0、manifest顺序FIFO，workers≤admission；正式精度策略仍为1/1/1。零服务通过既有异步四phase，不同步发布后继；任何失败停止新后继并排空实际在途服务。

HostInputs从SST头移至DAG公共头，方便不加载SST的元数据测试；Runtime仅增加可选service_key，将完整(query,op,stage)贯穿CPU服务记录，原默认key行为保持不变。新模块无旧Replay/Content/功能库链接依赖；主仓旧路径仍共存，清理属于P20。

输出是流式JSONL：configuration记录原始配置hash、有效解析路径、binding、实际registry和Profile；demand记录稳定键、Work/features、ns及prediction/measured-table来源与公式；service记录完整稳定键、提交/开始/结束/服务时间；stage/request记录依赖完成和窗口；summary记录需求总量、配额峰值、排空、无设备路径事实及错误。输出错误不会产生成功结论，失败summary的success=false；`full_acceptance_passed`始终false。CPU需求与排队、可见时间分开，不把宿主墙钟写为模拟耗时。

## 运行方式

这是P11的实际SST入口，统一安装CLI由P14实现：

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
# SST路径取 build/delivery/sst-paths.json 的 sst 字段
<SST> --add-lib-path=<绝对build目录> configs/memory_timing.py -- <run.json>
```

`run.json`必需字段：`format`、`package`、`scratch`、`report`、`report_sha256`、`package_sha256`、`registry`、`registry_sha256`、`profile`、`profile_sha256`、`target`、`context`、`workers`、`slots`、`admission`、`output`。target采用P05完整六字段，context为`resident_random/2`；fixture可显式 `allow_fixture=true` 并使用`fixture/1`。native-capture另需`semantic_report`与`semantic_sha256`。scratch与output必须为新路径，目录由调用方准备。可重放的具体配置及包/Profile见本轮证据，输入保持不可变。

## 实际检查与证据

最终证据：`results/v0.4.5/p11/run2/`，含reproduce.py、命令/cwd/退出码、stdout/stderr、包/报告/Profile/有效配置/trace、依赖核验、源码/二进制hash和迁移审计。P04/P05/P08分别312/58/635项登记产物hash全部匹配。测试为小规模开发证据，不是正式SIFT采集/拟合或CPU精度验收。

实际执行根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure`，**108/108通过**，包含6项SST内存测试及独立Plan测试；根原有P04/P05/P08相关回归继续通过。`NDPSIM_SANITIZE=ON`配置并构建memory_plan_probe/timing_runtime_probe/dag_executor_probe/cost_model_probe，定向CTest **4/4通过**。P01设计 **11/11**、P00 **3/3**，迁移审计 **375文件/108CTest** 通过。`nm -C`/`otool -L`检查内存模块无Content、搜索功能、旧Replay和设备构造符号，实际动态依赖仅SQLite/libc++/libSystem；EventBridge静态链接的MQSim Engine不表示创建设备。子模块HEAD、状态、实际源码hash及MQSim相对锁定原生commit diff另存；本轮没有修改MQSim/SST。最终`git diff --check`与文档链接检查通过。

保留dev1～dev4和run1失败/中间样本。最初系统python缺jsonschema，改用deps.lock.json锁定Python；测试相对路径、SST未获配置参数、实测表duration_ns/quality字段均在夹具中修正。run1的107/108来自unittest把导入的Memory测试类重复发现导致证据目录冲突；改为仅导入模块，并在新run2目录完整重跑，未复用该失败为通过。未弱化调度或模型拒绝断言。

- [SST测试](../tests/test_v045_memory.py)：四查询fork/join，1/2/4 worker与slots、0/3/17ns模型，1/1/1手算总时长分别0/48/272ns；slots及admission独立限制，四种依赖门控、终态回收。服务稳定键逐项关联需求，禁止把多worker吞吐当CPU精度。
- 从本轮重构建原生程序采集6节点×2维、11查询小图，独立语义/Work校验后删除全部native payload文件，实际消费同一新包的385阶段；state/join为0，其余合成常数2ns，独立求和与SST完成时间相同。模型用于接线测试，不代表拟合预测。
- 同包更换模型改变模拟时间；增加2000个Profile来源hash的解析/散列负担，另在独立进程持续执行SHA256制造宿主CPU竞争，完整需求/服务/阶段/请求/排空事件保持一致。此检查不声称墙钟或RSS不变。
- 实测逐调用表正确binding通过，改worker配额、删调用、改Work拒绝；包/报告/registry/Profile hash、目标身份、上下文、未知payload配置、非整数或0配额、fixture未允许、缺模型和超范围拒绝且不出成功summary。
- 独立Plan探针不加载SST，遍历16个已验证stage，独立求和0/48/272ns及hash篡改拒绝，用于ASan覆盖实际配置/模型/lowering；P07/P08测试另验执行和失败生命周期。

## 限制

没有运行SIFT1M tuning32/evaluation128正式采集、拟合、benchmark或两点精度比较；AC03仍需后续SSD接线/清理，AC04留待P15/P18。没有实现SSD映射、Controller、传输、统一CLI或三层Validator。本次止于P11，不开始P12，不提交或推送Git。

仅支持单rank/thread、只读静态包与resident上下文；不支持动态放置、反事实控制流或跨query可变状态。结构/语义receipt的信任边界继承P08，hash不是恶意生产者认证。Profile/registry及快照整体驻留（每文件最大64MiB），包/活跃图预算继承P08；不是硬RSS/磁盘沙箱，trace磁盘预算及统一运行前预检留给P14。配置和输入在读取期间应保持不可变，output新路径检查不是恶意并发路径替换防护。SSD/PCIe/Controller仍为configured_unvalidated，不预测下沉收益。
