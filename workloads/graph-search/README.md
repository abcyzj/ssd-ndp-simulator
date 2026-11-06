# 独立图搜索参考工程（P03）

C++17/POSIX 原生 `ndpsim-ranked-expansion-v1`，只依赖标准库、线程和显式提供的 [Capture SDK](../../sdk/README.md)。macOS arm64 已验证；Linux 尚未实测。无需模拟工程、SST、MQSim、设备 XML、构建缓存或 Python 生产运行时。Python 3.9+ 仅用于测试。

## 构建与验证

可只复制本目录和 `sdk/` 至任意目录（包括带空格路径）：

```sh
cmake -S workloads/graph-search -B build/graph -DNDPSIM_SDK_ROOT="$PWD/sdk" -DBUILD_TESTING=ON
cmake --build build/graph -j2
ctest --test-dir build/graph --output-on-failure
build/graph/ndpsim_native --help
```

不设 SDK 源路径时必须通过 `CMAKE_PREFIX_PATH` 提供已安装的 SDK，不自动搜索父工程：

```sh
cmake -S sdk -B build/sdk -DCMAKE_INSTALL_PREFIX="$PWD/build/sdk-prefix"
cmake --build build/sdk -j2
cmake --install build/sdk
cmake -S workloads/graph-search -B build/graph-installed -DCMAKE_PREFIX_PATH="$PWD/build/sdk-prefix" -DBUILD_TESTING=OFF
cmake --build build/graph-installed -j2
```

功能模块分别为 `ndpsim_graph_values`（字节、错误、SDK hash 适配）、`ndpsim_graph_operators`（L2、候选排序）、`ndpsim_graph_algorithm`（frontier、去重、合并）。头文件位于 `src/`。没有 ContentStore、Layout、句柄、Runtime 或模拟资源依赖。根构建暂时直接复用本目录的功能源码；旧模拟功能链接留待 P20 删除。

## 自身运行入口

`--help` 列出参数，所有数值为 uint32 十进制；文件路径可包含空格，调用时须加引号。默认 benchmark，JSON 结果写 stdout，错误写 stderr 并退出1。程序读取并核对每个文件的精确大小和 SHA256：

```text
ndpsim_native N D Q R truth_k entry k L B E chunk workers admission
  vectors sha256 queries sha256 graph sha256 truth sha256 query_ids sha256
  [uninstrumented | cpu_sample directory total_bytes buffer_bytes stride |
   static_capture directory total_bytes buffer_bytes 1] [--semantic-log | --detail8]
```

- vectors：N×D、queries：Q×D，FP32 little-endian，无 fvecs 行头。
- graph：N 行固定 `(R+1)` 个 uint32 little-endian；首项 degree，随后有效邻居，其余为 padding。
- truth：Q×truth_k 个 uint32；query_ids：Q 个原始 uint32 查询 ID。保持查询顺序。truth 用于 Recall，不参与搜索决策。
- k≤L、k≤truth_k；worker 取1/2/4，admission≥worker。单 worker/admission=1 为正式 CPU 比较基准，其余仅诊断。
- benchmark 不安装逐操作采集／测量探针，保留查询级 wall/thread CPU 计时和 Work 统计；uninstrumented 额外关闭查询级 CPU 探针，供扰动比较。
- cpu_sample 与 static_capture 分别运行。directory 须由调用方预建且为空，每查询写原始 shard；失败保留目录，重跑新目录。预算为总输出字节和每查询缓冲字节；cpu_sample stride 可取正整数，完整 static_capture 使用1。
- `--semantic-log` 导出全查询语义 trace；`--detail8` 只附前8查询诊断 trace，均不能代替完整 IR。

`tests/test_native_search.py <absolute-binary>` 会生成临时11查询夹具，运行4组 worker/admission 并以独立 Python oracle 检查 top-k、Recall、七项 Work、计数与接纳。`tests/test_search_core.py --probe <absolute-probe>` 检查60组搜索的逐阶段 packed 值和 Work；额外检查 FP64 固定维序、极值、舍入与无效输入。

算法、容器、计数、FP32 输入与固定维序 FP64 L2 保持原参考实现；所有功能目标显式禁 fast-math/FMA。`--build-info` 三行分别为来源指纹、编译器及有效编译选项。独立指纹包含实际工作负载源码/CMake和链接 SDK archive hash；根迁移构建有自己的来源指纹，不能混用两者的测量。二进制 hash、实际 flags/SDK hash 仍应随实验单独记录。

## 当前边界

P03 保留旧原生 raw observation 能力，`static_capture` **不是 `ndpsim-static-ir/2` 包**，不能交给新仿真入口。新 SDK 的图阶段接线、完整特征/依赖、Measurement/2、封包及独立语义校验由 P04 实施。这里不调用旧 Python 封包工具，也不要求历史 results。

未运行 SIFT1M 全规模、A/B/A、正式采集、CPU 拟合或精度验收。本工程身份变化后必须重采／重拟合，旧表不能直接继承。正式数据准备和预算说明参见 [SIFT1M](../../docs/sift1m.md)；原始锁定 NSG/prepared 尚缺的状态见 [P00](../../docs/baseline-v0.4.5.md)。大规模运行前须重新检查输入和资源。

本工程与 SDK 使用 MIT License，见各自 LICENSE；不分发第三方模拟器或数据集。
