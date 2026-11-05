# SSD NDP Simulator

单 SSD、主机控制的只读图 ANNS 仿真平台。SST 统一模拟时间，MQSim 模拟存储和共享 PCIe；真实平方 L2 结果决定后续访问，距离计算支持主机或盘控放置。硬件参数均为 `configured_unvalidated`。

## V0.4 开发入口

V0.4 需求已改为“独立原生搜索真实执行并插桩采集完整静态 IR → 仿真器按依赖与资源执行”，静态包契约与离线预检已冻结，原生采集已实现，[内存驻留回放入口](docs/replay-v0.4.md)已实现。新 A/D/O 验收显式使用静态路径，采集时戳不控制仿真；真实值与工作量须重算核验，语义或输入改变须重新采集。旧动态 `ir`、`direct` 及日常默认保持兼容。具体契约与边界见 [PRD §6.4](docs/prd-v0.4.md#64-静态-ir-输入依赖执行与资源调度)。

V0.4 的 [V04-0开工冻结](docs/start-freeze-v0.4.md)、[V04-1a纯算法核心](docs/core-v0.4.md)、[V04-1b原生构建与运行](docs/native-v0.4.md)、[V04-1c参考路径语义出口](docs/reference-semantics-v0.4.md)、[V04-2a计时计费与静态包契约](docs/contract-v0.4.md)、[V04-2b Profiler](docs/profiler-v0.4.md)、[V04-2c训练采集与 A/B/A](docs/training-v0.4.md)、[V04-3a模型接口与失败关闭](docs/cpu-profile-v0.4.md)、[V04-3b训练拟合与冻结](docs/cpu-fit-v0.4.md)、[V04-3c静态执行与内存驻留](docs/replay-v0.4.md)、[V04-3d worker 与 CPU 顺序](docs/replay-v0.4.md#worker-与-cpu-顺序)及 [V04-4a Validator 框架](docs/validator-v0.4.md)已完成。[V04-4b Level A](docs/level-a-v0.4.md) 已完成，AC-04-04 按两点口径通过。先读 [V0.4 PRD](docs/prd-v0.4.md)、[可执行任务清单](docs/implementation-plan-v0.4.md)和[源码定向复核](docs/source-audit-v0.4.md)。范围为现有图搜索的独立原生程序、Profiler、CPU Profile、内存驻留单线程精度验证及现有L2下沉条件预测；2/4 worker为诊断，SSD/PCIe/Controller仍未校准。新增Profile和驻留路径须显式启用，不改变下述V0.3日常默认。常规验收采用 [14个模拟配置](docs/validation-matrix-v0.4.md)，重复/采集和定向/继承测试另计，原52条保留为按需深度回归；V0.4 总验收入口尚待实现。

## V0.3 可运行基线

V0.3 已让现有 SIFT1M 图搜索通过动态、进程内 IR 执行，并保持搜索结果、模拟时序和资源账本与 direct 基线等价。继承设计见 [V0.3 PRD](docs/prd-v0.3.md) 和 [IR 设计规范](docs/ir-v0.3.md)，再从[文档索引](docs/README.md)按任务选择接口与测试说明。

V0.3 阶段计划、收口和矩阵证据已收拢到[归档索引](docs/archive/v0.3/README.md)，总交付结论见[V0.3 交付记录](docs/releases/v0.3.md)。现行 IR 接口按[文档入口](docs/README.md)选读；工具、配置和独立断言继续用于继承回归。历史完成状态不计入 V0.4 进度。

当前可运行交付为 [V0.3](docs/releases/v0.3.md)，V0.2 继承范围与历史证据见 [V0.2 交付记录](docs/releases/v0.2.md)。数据/图/真值准备和固定矩阵见 [SIFT1M](docs/sift1m.md)，继承回归见[测试说明](docs/testing.md)。V0.3 配置省略 `execution_engine` 走 ir；设为 `"ir"` 可运行动态 IR，设为 `"direct"` 保留对照。有效 engine 写入 manifest/config，IR 另写 ir-manifest.json；无效值拒绝。以下未指定 engine 的配置使用 ir；自动化旧基线显式固定 direct。

V0.2 阶段计划、收口记录及一次性探针已移入[归档](docs/archive/v0.2/README.md)，不作为新版本待办。冻结查询、预算、实验配置及有效回归继续保留；原始数据、准备产物、历史结果和构建缓存仍在本机忽略目录，新检出须按说明重建。

## 只运行实验

已有构建产物时，使用独立实验入口：

```sh
python3 tools/run_experiment.py
```

默认复用 `build/delivery`，以相同数据运行 host/controller，输出在 `results/experiment/{host,controller}/`。该入口只准备实验输入、执行 SST/MQSim、导出结果和统计，不调用 CMake、CTest、验收脚本或两放置核对器。构建产物缺失时直接报错，不自动编译。

```sh
# 指定已有构建，单独运行盘控模式
python3 tools/run_experiment.py --build build --mode controller --output results/controller-only
# 使用自己的配置和已有数据，运行两种模式
python3 tools/run_experiment.py --build build --config configs/experiments/host.json --data results/controller-only/controller/data --output results/pair-2
```

`--mode pair|host|controller` 默认 `pair`，会覆盖配置中的 placement。输出目录必须不存在；重跑使用新的 `--output`。省略 `--data` 时按配置生成数据及计算 Recall 所需的离线精确真值；传入时复用已有数据和 `truth.json`。`--event-log` 只开启 JSONL 导出，不增加对照验证；也可在配置中设置 `event_log`。各模式保留配置、数据/源码/依赖哈希、top-k、Recall、模拟性能和 JSON/CSV 统计，口径见 [输出说明](docs/output.md)。保留运行所需的输入合法性和输出完整性检查。

## 构建、测试与交付入口

在仓库根目录、依赖和系统工具已准备好的条件下运行：

```sh
python3 tools/run.py
```

默认通过根 CMake 配置并编译，运行 CTest，再以同一份固定 seed 数据分别执行 host/controller。输出在 `results/delivery/{host,controller}/`；控制台给出阶段命令，详细输出在对应 `.log`。已有构建可复用，输出目录必须不存在；重跑时用 `--output results/delivery-2`。单独模式用 `--mode host` 或 `--mode controller`，均经过同一构建入口。默认 JSONL 关闭；加 `--event-log` 会记录事件并对两放置逐批决策和流量账本进行独立核对。

默认 N=2048、d=32、R=16、Q=32、seed=42、k=10、L=32、B=1、E=64、chunk=16、max_inflight=1；算法、数值和布局详见 [图示例](docs/search.md) 与 [两种放置](docs/controller.md)。Recall 真值由离线穷举生成，默认实测 Recall@10=0.2875；不以 Recall=1 或盘控更快为门槛。

## 环境与干净复建

已验证平台为本机 macOS / arm64。预先准备 CMake 3.22+、C/C++17 编译器、Git、GNU Make、Autoconf、Automake、GNU Libtool、GNU M4，以及 Python 3.9+ 配套的解释器、开发头文件/库和 `python-config`；完整 AC 验收入口还要求 Python 3.12+ 的安全 tar 解包接口，文档使用锁定的 Python 3.13。完整本机工具版本见 [deps.lock.json](deps.lock.json)。PATH 的系统 Python 3.9 与 Miniconda 的开发库不能混用。文档验证使用锁定的 Miniconda Python 3.13.12；SST 默认从锁定配置选择同前缀 Python。脚本不安装系统软件、不下载依赖、不执行 Git 初始化/升级。

源码须包含主仓 gitlink 指定且已初始化的 `third_party/MQSim` 和 `third_party/sst-core`。平台不依赖 sst-elements。MQSim 实际 fork HEAD 与原生 oracle commit 是不同身份；不能只拿原生基线替代 fork，见 [维护说明](docs/mqsim-source-management.md)。

以下一条命令要求平台、ASan/slow 构建目录、显式 SST 缓存及输出均不存在，会从固定源码重新构建 SST、平台并运行两种模式、全部 AC、独立原生 oracle 与 ASan 探针：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/run.py --clean-build --build build/v0.1-clean --sst-cache .cache/v0.1-clean-sst --output results/v0.1-clean --event-log --verify-ac
```

复跑须换一组目录名。`--clean-build` 只检查目录，不删除已有内容；干净构建不等于全新操作系统，仍依赖预装工具和现有锁定子模块。完整方法、交付清单、输出解释与 AC 索引见 [复建交付说明](docs/delivery.md)。

## 构建入口与配置

独立手工构建入口也保持可用：

```sh
cmake -S . -B build
cmake --build build -j2
ctest --test-dir build --output-on-failure
python3 tools/run_config.py --config configs/experiments/host.json --build build --output results/host
python3 tools/run_config.py --config configs/experiments/controller.json --build build --output results/controller --data results/host/data
```

CMake 在配置阶段调用 [prepare_sst.py](tools/prepare_sst.py)，只在 `.cache/sst/<配置键>/` 的固定源码副本执行 autogen、configure、make、install，SST 子模块不修改。有效缓存命中跳过准备；不同平台构建共享同配置安装。删除 `build` 不删除 SST 缓存；相同配置通过文件锁串行准备。工具链/Python/SDK/编译参数改变使用不同配置键。首次构建需等待 SST 完成；不依赖 M0.2 的安装或旧 build。MQSim 直接编译子模块源码，不应用 patch 栈。

| CMake 参数 | 用途 |
| --- | --- |
| `-DNDPSIM_SST_CACHE_DIR=/absolute/cache` | SST 共享缓存根；默认仓库 `.cache/sst`；统一入口对应 `--sst-cache` |
| `-DSST_ROOT=/absolute/prefix` | 复用同架构/ABI 的 SST 16.0.0 安装，跳过准备；此方式不证明 SST 干净重建 |
| `-DPython3_EXECUTABLE=/absolute/python3` | 选择配套 Python；可用 `NDPSIM_PYTHON_CONFIG` 指定 python-config |
| `-DNDPSIM_SST_BUILD_JOBS=2` | SST 内部 make 并发数；外层 `--jobs` 控制平台编译 |
| `-DCMAKE_BUILD_TYPE=Debug` | 默认 RelWithDebInfo |
| `-DBUILD_TESTING=OFF` | 手动构建时关闭测试；统一交付入口固定开启 |
| `-DNDPSIM_SANITIZE=ON` | 独立 ASan 探针；不是完整 SST/上游泄漏审计 |
| `-DNDPSIM_SLOW_FUNCTIONAL=ON` | AC-10 等价慢 L2 实验，非硬件参数；交付入口固定 OFF |

macOS 默认用 xcrun 选择 SDK。`build/sst-paths.json` 记录实际 SST 路径；缓存中的 `receipt.json`、`commands.json` 和 `logs/` 记录工具链、源码/安装哈希和构建命令。`libndpsim.so` 为 SST 元素库。安装缺失或损坏时重新配置会校验并修复；不要在构建或实验仍使用缓存时删除它。

## 扩展、验收与边界

- [扩展指南](docs/extensions.md)：实际 C++ 接口、算法/算子接入、数据布局和观测变更。
- [完整验收](docs/testing.md)：`/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_acceptance.py`；全部 AC 的输入、断言及复现入口。`--quick` 仅为小矩阵。
- [输出口径](docs/output.md)：并发 1/2/4、接纳、JSON/CSV、可选 JSONL、分位数及资源统计。
- [模型限制](docs/model-limitations.md)：未校准参数、缓存/初态、独立缓冲、失败恢复和 Unsupported 能力。
- [MQSim 改动与维护](docs/mqsim-source-management.md)、[MIT 许可证](LICENSE)。平台代码和文档使用 MIT License，第三方依赖保留各自原有许可证。
