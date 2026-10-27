# SSD NDP Simulator

V0.1 的单 SSD / SST + MQSim 仿真平台。目前完成库化、生命周期骨架、M0.3a 事件桥及 M0.3b 本地读取 PoC，以及 [M0.4 最小契约冻结](docs/m0.4-minimal-contract.md)；正式 I/O 集成、ANNS 和性能验收尚未实现。任务状态见 [任务清单](docs/v0.1-tasks.md)。

## 构建与检查

当前验证环境为 macOS / Apple Silicon，使用 CMake 3.22+、C/C++17 编译器、Git、GNU Make、Autoconf、Automake、GNU Libtool、GNU M4，以及 Python 3.9+ 的解释器、开发头文件/库和配套 `python-config`。本机已验证的工具版本见 [deps.lock.json](deps.lock.json)。依赖工具须预先安装；CMake 不安装系统软件或下载依赖。

源码检出须包含已初始化的 `third_party/MQSim` 和 `third_party/sst-core` 子模块。然后只需：

```sh
cmake -S . -B build
cmake --build build -j2
ctest --test-dir build --output-on-failure
cmake --build build --target run_lifecycle
```

不需要先运行 `tools/macos_m0_2.py`。CMake 在配置阶段调用一个 [prepare_sst.py](tools/prepare_sst.py) 脚本，从锁定的 SST commit 准备源码，在仓库固定的 `.cache/sst/<配置键>/` 中执行 Autotools 引导、配置、编译和安装，然后导入 SST，再编译元素库。首次 `cmake -S . -B build` 会等待 SST 准备完成；后续配置命中有效缓存时直接复用，不执行 autogen、configure、make 或 install。不同平台构建目录在配置一致时共用同一份安装，删除或清理 `build` 不会删除 SST 缓存；缓存不写入系统目录；SST 子模块不被 autogen 修改。MQSim 直接从 `third_party/MQSim` 编译，保留其开发改动。

默认结果为 `build/libndpsim.so`、`build/lifecycle_probe` 和 `.cache/sst/<配置键>/install/bin/sst`。`run_lifecycle` 会自动使用选定的 SST 安装和当前构建的元素库。正常输出应包含 `finish SST=1 ns MQSim=0 ns; device destroyed`；1 ns 仅为生命周期调度探针。CTest 共十三项，包含生命周期、事件步进、原生事件循环、本地读取、SST E1、元素加载与 SST 生命周期检查。事件桥实现、复现和范围见 [M0.3a 记录](docs/m0.3a-event-bridge.md)。

## 可选构建设置

| 参数 | 用途 |
| --- | --- |
| `-DNDPSIM_SST_CACHE_DIR=/absolute/cache` | 指定共享缓存根目录；默认是仓库内 `.cache/sst`，相对路径按仓库根目录解析 |
| `-DSST_ROOT=/absolute/sst/prefix` | 显式复用已有 SST 16.0.0 安装，跳过 SST 构建；其架构和 C++ ABI 须与平台一致 |
| `-DPython3_EXECUTABLE=/absolute/python3` | 指定 SST 使用的 Python，须有同前缀的开发库和 `python-config`；后者也可用 `NDPSIM_PYTHON_CONFIG` 指定 |
| `-DNDPSIM_SST_BUILD_JOBS=2` | SST 内部 make 并发数，独立于外层 `cmake --build -j` |
| `-DCMAKE_BUILD_TYPE=Debug` | 设置构建模式；默认 RelWithDebInfo |
| `-DBUILD_TESTING=OFF` | 不构建测试探针或注册 CTest 测试 |
| `-DNDPSIM_SANITIZE=ON` | 为后端添加 AddressSanitizer；运行无 I/O 后端探针，未包含泄漏审计 |

macOS 默认通过 `xcrun` 选择 SDK，也可显式设置 `CMAKE_OSX_SYSROOT`。本机存在锁定的 Python 路径时优先使用它；其他环境使用 CMake 查找到的 Python，须满足解释器、开发库和配置程序配套。SST 默认禁用 MPI、HDF5 和 ncurses；当前平台只支持单 rank/线程。

构建路径记录在 `build/sst-paths.json`，SST 安装回执为 `.cache/sst/<配置键>/receipt.json`，阶段命令及日志位于同目录的 `commands.json` 和 `logs/` 下。脚本核对固定源码、工具链、Python、SDK、编译参数及已安装文件的哈希；安装文件缺失或损坏时可通过 Make 增量构建并重新安装。锁定 commit、准备脚本、工具链或编译配置变化会使用独立的构建缓存和安装前缀，已有构建仍指向原来的安装；并发数变化不使编译缓存失效。同一配置的并发准备通过文件锁串行化，等待者随后校验并复用安装。缺少工具、Python 不配套或 SST commit 不符会明确失败。

首次切换到共享缓存需重新构建一次 SST；旧构建目录中的 `_deps` 不会自动迁移。常规命令保持不变：`cmake -S . -B build-other` 会复用相同配置的共享安装。需要彻底重建 SST 时，可在没有构建或仿真使用它时删除对应配置键目录，再运行 CMake。

[M0.2 原生基线工具](tools/macos_m0_2.py) 仅用于独立原生 oracle 与历史环境回归，不是平台构建前置条件。[M1.1 验证工具](tools/verify_m1_1.py) 可额外核对既有原生回执并运行完整回归；其默认平台构建同样由 CMake 准备 SST，可通过 `--sst-root` 显式复用已有安装。MQSim 维护约定见 [源码维护说明](docs/mqsim-source-management.md)，模型与生命周期限制见 [M1.1 说明](docs/m1.1-library-lifecycle.md)。

M0.4 契约复核（手算数据流/账本、源 ABI 和文档检查，不是性能模拟）：

```sh
python3 tools/verify_m0_4.py --output results/m0.4-check
```

输出目录须为新目录；可加 `--ctest-build build` 对已有构建运行回归。固定输入及后续实现边界见 [契约](docs/m0.4-minimal-contract.md)。
