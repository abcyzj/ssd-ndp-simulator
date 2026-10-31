# 仓库协作约定

- 当前开发目标为 V0.2。先读 README.md、docs/prd-v0.2.md、docs/v0.2-implementation-plan.md，
  再按任务从 docs/README.md 选择现行规范、接口与测试说明；docs/releases/v0.1.md 用于核对继承基线和验证证据，不要求通读历史。
  docs/prd-v0.1.md 保留 V0.1 需求基线；docs/archive/v0.1/ 仅供按需追溯，阶段指令和状态不继续生效。
- 按 V0.2 实施计划第 0、6 节推进 T0～T6、T8（T7/M3 已取消）：先收口 T0/M0 的数据身份、锁定建图依赖与本机验证、资源预算和初始化测量，
  再按依赖推进真实数据小规模贯通、SIFT1M 和交付；每次以实际产物/证据更新阶段状态，不把规划或历史通过当成本轮完成。
  计划默认方案作为实施起点，待核实项保留出口门槛；局部门槛未满足时继续不依赖它的工作，不重新进行整轮需求规划。
- V0.2 接入真实 benchmark，以 SIFT1M 为交付数据集，取消 Deep10M 适配计划；继承单 SSD、应用只读 ANNS、主机控制搜索、主机/盘控计算及 SST/MQSim 边界。
  计划目标不等于当前已实现能力。改变范围或模型结论须说明影响；任务要求、现行文档、代码和测试冲突时明确差异并处理，不能用历史方案直接覆盖当前行为。
- SST 主导模拟时间；MQSim 不得越过 SST now。等待用异步 continuation，不阻塞事件引擎。
  真实结果驱动后续访问；服务及必要传输完成前结果不可见，不以固定完整 trace、随机距离或墙钟替代闭环和性能模型。
- 区分逻辑内容、LBA 和驻留；每项资源/流量只有一个计费者。近存向量读取不得先经完整主机回传。
  现行时间、消息与计费边界见 docs/contracts.md，限制见 docs/model-limitations.md。
- MQSim 适配直接修改并编译 third_party/MQSim，不维护构建时 patch 栈或另一份集成源码。
  原生 oracle 独立从 deps.lock.json 锁定原生 commit 导出；SST Core 不修改；依赖改动最小并保留许可证。
- 依赖记录完整 commit；未提交 MQSim 改动记录相对原生基线 diff 哈希及实际源码哈希。
  提交时先保存 MQSim fork commit 再更新主仓 gitlink，不能遗漏子模块实现；不自动提交、推送、升级或初始化 Git。
- 构建以根 CMake 为入口，自动调用 tools/prepare_sst.py 在 .cache/sst 固定源码副本中准备 SST；
  NDPSIM_SST_CACHE_DIR 可覆盖缓存，SST_ROOT 可复用安装。不要依赖先跑 M0.2 或旧 build 安装。
- 常规验证：`cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、
  `ctest --test-dir build/delivery --output-on-failure`。完整 AC 用锁定 Python 运行 `tools/verify_acceptance.py`，细节见 docs/testing.md。
  持续测试位于 tests/、configs/regression/，工具入口见 tools/README.md；tools/archive/v0.1/ 仅供阶段复现，不因归档而移除有效回归或弱化断言。
- 按变更运行有意义的验收，记录输入、配置、命令、退出码、输出及断言。区分静态核查、PoC、quick subset 与完整 AC；未执行不标通过。
  参数未校准、模型简化及 Unsupported 必须明示，不预设盘控更快或声称绝对性能已校准。
