# 仓库协作约定

- 下一版为用户指定的 V0.4.5，正式需求见 docs/prd-v0.4.5.md；需求、源码核查（docs/source-audit-v0.4.5.md）和22项实施计划（docs/implementation-plan-v0.4.5.md）已完成规划，P00 基线与迁移清单（docs/baseline-v0.4.5.md）、P01 契约与风险设计（docs/contract-v0.4.5.md）、P02通用SDK/校验（docs/capture-sdk-v0.4.5.md）、P03独立图搜索构建（docs/native-v0.4.5.md）、P04新IR采集与语义校验（docs/capture-graph-v0.4.5.md）、P05通用模型与注册接口（docs/cost-model-v0.4.5.md）、P06独立拟合／制表（docs/fit-v0.4.5.md）、P07元数据Runtime与资源服务（docs/runtime-v0.4.5.md）、P08静态DAG执行器（docs/dag-v0.4.5.md）、P09无内容Layout（docs/layout-v0.4.5.md）、P10 MQSim运行时配置（docs/device-config-v0.4.5.md）、P11内存timing-only接线（docs/memory-timing-v0.4.5.md）、P12元数据传输与Controller资源（docs/controller-timing-v0.4.5.md）、P13图搜索SSD两放置（docs/graph-ssd-v0.4.5.md）、P14统一CLI/报告（docs/cli-v0.4.5.md）、P15三层Validator（docs/validator-v0.4.5.md）和P16独立MQSim对拍（docs/mqsim-oracle-v0.4.5.md）已完成，P17～P21 未开始，全部新AC未验收，当前可运行交付仍为 V0.4。开展 V0.4.5 工作先读新 PRD、源码核查和实施计划，按 P00～P21 依赖及证据出口推进，其 §2 明确替代下列 V0.4 的旧路径兼容、模拟侧功能重算、原生构建入口及历史回归义务；未替代的时间、资源、独立核对、数据隔离与证据约束继续适用。以下 V0.4 条目用于理解现状和复现旧版，不得据此阻止新 PRD 已明确授权的迁移，也不得把旧版通过计为 V0.4.5 完成。

- 当前可运行交付为 V0.4，正式范围、复现证据与限制见 docs/releases/v0.4.md；V0.3 为继承基线。先读 README.md、docs/prd-v0.4.md、docs/implementation-plan-v0.4.md 和 docs/source-audit-v0.4.md；继承 IR 语义先读 docs/prd-v0.3.md、docs/ir-v0.3.md，再按 docs/README.md 选读现行接口与测试。
- V0.4 任务按实施清单的依赖推进，每项保存需求来源、输入/配置/源码/构建身份、命令/退出码、输出、断言和 hash 后更新状态。规划、静态核查、PoC、quick、历史通过与完整 AC 分开；局部精度未达时继续不依赖它的工作，不能记总通过。
- V0.4 只做现有 SIFT1M/NSG 图及 ndpsim-ranked-expansion-v1 的独立原生程序、Profiler、CPU Profile、真实搜索插桩采集完整静态 IR、依赖驱动执行与内存驻留、Validator 和现有 L2 下沉条件预测。严格精度出口为当前 Mac 的内存驻留单线程 Host；2/4 worker 仅诊断。不得加入 DiskANN/PQ、第二负载、Deep10M、新计算位置、精细 Cache/DRAM 模型或真实 SSD/盘控校准。
- 保留 direct 冻结参考与独立数值/搜索/账本核对器；纯函数共享不能仅靠两条共用实现互证。保持算法、容器/计数、FP32输入与固定维序FP64 L2、禁 fast-math/FMA。V0.3 完整交付与默认IR证据见 docs/releases/v0.3.md，不沿用为 V0.4 完成状态。
- 原生仅构建/运行不得依赖 SST/MQSim。训练 tuning32 与独立 evaluation128 按原始 query ID 隔离；评价后调参须新版本并披露泄漏。验收矩阵允许冻结清单中的阶段在两切分各采逐调用 CPU 耗时并按身份/Work 查表。评价表仅供对应仿真执行，原生对拍基准另跑，评价数据禁止回流拟合。先冻结互斥 CPU 桶、取数/复制/解码/分配边界和适用条件，不能以 QPS 或墙钟残差补成本；模型缺失、超范围、身份不符或采样不完整失败关闭。
- 静态执行、新 CPU Profile 和目标内存驻留必须显式启用；content_backend=memory 不是无SSD路径。旧配置保持默认动态ir、SSD路径和原成本，旧自动化参考显式direct；原默认时序/账本仍严格等价。按修订PRD，V0.4新增A/D/O使用完整静态IR，诊断detail8不能代替全查询执行输入；A内存CPU验收与B/C SSD下沉分列，只用B/C求收益。
- SST 是唯一模拟时间权威，MQSim 不越过 SST now。等待用异步 continuation；静态路径按已采集拓扑、依赖和本次真实值/Work核验门控后继，失配即停止，输入、服务和必要返回完成前不可见。不得以采集时戳、预填结果、随机距离或墙钟代替服务；语义/数据变化须重采，旧动态ir继续真实结果闭环。静态包schema/执行入口已实现；不将旧日志冒充可执行IR。
- 区分逻辑内容、LBA、驻留和各层字节；每项资源/流量只有一个计费者。近存向量不得先完整回传Host。遵循 docs/contracts.md、docs/controller.md 和 docs/model-limitations.md；新Profile的计费变更仅在显式配置生效并同步规范。
- 直接修改并编译 third_party/MQSim；不维护构建时patch栈或另一份集成源码。原生oracle从 deps.lock.json 锁定原生commit独立导出。SST Core不修改，依赖改动最小并保留许可证。
- 依赖记录完整commit、MQSim相对原生diff和实际源码hash。提交时先保存MQSim fork commit，再更新主仓gitlink，不遗漏子模块实现；不自动提交、推送、升级或初始化Git。
- 模拟构建以根CMake为入口，由 tools/prepare_sst.py 在 .cache/sst 固定源码副本准备SST；NDPSIM_SST_CACHE_DIR可覆盖，SST_ROOT可复用安装，不依赖旧阶段安装。原生仅构建使用根 CMake 的 `-DNDPSIM_NATIVE_ONLY=ON`，运行入口见 docs/native-v0.4.md。
- V0.4常规完整规模验收采用 docs/validation-matrix-v0.4.md 的12个模拟配置；原生/模拟重复、采集和定向/继承测试另计。52条及配套效率/Q梯度为按需深度回归，影响不明或触发而未通过时阻塞相关交付；保留旧工具的52条完整性断言，不把12配置冒称完整V0.3验收。
- 常规验证：`cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure`。完整AC用锁定Python运行 tools/verify_acceptance.py；完整继承与V0.4出口见 docs/testing.md 和实施清单。保持 tests/、configs/regression/ 及有效独立断言，不因归档或版本名弱化回归。
- 完整规模采集前重新核验输入、RAM/磁盘/输出预算和超时；保留失败样本，新目录重跑。CPU精度只适用于记录条件；SSD/PCIe/Controller仍 configured_unvalidated，Unsupported 明示，不预设下沉更快。
- docs/releases/v0.2.md 用于继承证据；docs/prd-v0.1.md 保留需求基线。docs/archive/v0.1/、v0.2/ 和旧阶段计划按需追溯，阶段指令不继续生效；configs/benchmarks/、configs/experiments/v0.2/、tools/verify_t2.py～verify_t6.py仍是有效继承基线。需求、现行文档与代码冲突时说明差异及影响，不用历史方案覆盖现状。

- V0.3 阶段文档与继承工具/配置说明统一从 docs/archive/v0.3/README.md 追溯；PRD/IR总规范及有效接口、工具、配置和测试继续约束继承行为。本机结果实体在 results/archive/v0.3，results/v0.3 保留兼容链接；不得把归档或历史通过计为V0.4完成。
