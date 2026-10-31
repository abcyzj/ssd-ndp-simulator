# SSD NDP Simulator

V0.1 是单 SSD、主机控制的只读图 ANNS 仿真平台。SST 统一模拟时间，MQSim 模拟存储和共享 PCIe；真实平方 L2 结果决定后续访问，可将距离计算放在主机或盘控。V0.1 交付范围与本次验证见[版本交接](docs/releases/v0.1.md)，按需阅读[现行文档索引](docs/README.md)；阶段过程见[历史归档](docs/archive/v0.1/README.md)。示例是简化图搜索，参数均为 `configured_unvalidated`。

## V0.2 交付入口

当前进入 V0.2 开发，需求依据为 [V0.2 PRD](docs/prd-v0.2.md)，执行顺序与阶段门槛见 [V0.2 实施计划](docs/v0.2-implementation-plan.md)。目标是基于 SIFT1M 的真实 benchmark 接入与交付，Deep10M 已移出 V0.2 范围，继承单 SSD、只读、主机控制搜索和 host/controller 对照边界。

T0～T6 与 T8/M4 已完成；完整 SIFT1M 的固定查询矩阵、交付轮完整回归和新运行复现已验收，见 [V0.2 交付记录](docs/releases/v0.2.md)。交付入口为 `tools/verify_v02.py --all`，完整命令、前置准备与复用规则见 [交付说明](docs/delivery.md)。本轮 CTest 67/67、完整 V0.1 AC-01～11、独立锁定原生 oracle、17项 ASan、T3/T4/T5 复验、26次 SIFT 运行严格复用后的473条断言及两放置新复现通过；AC-02-10=N/A，Deep10M已取消。参数仍为 `configured_unvalidated`，不承诺绝对硬件性能已校准。

数据/图/真值准备与各阶段证据可从 [文档索引](docs/README.md) 追溯。支持默认 mmap/公式布局、受控2/16GiB profile、事件流式输出与终态回收；SIFT交付使用2GiB profile和完整1M base、固定32/128查询子集，不是全10k查询测试。原始数据、准备、结果与构建为本机忽略产物，新检出须按说明取得锁定输入并重建。下述V0.1用法继续有效。

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
