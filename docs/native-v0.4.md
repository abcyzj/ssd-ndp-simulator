# V04-1b 原生构建与运行

独立程序实现 FR-04-N02/N03/N04 和 PRD §7.1 的固定批次窗口。原生仅构建在配置阶段分流，只编译 `ndpsim_native` 及其内容、算子和纯搜索核心依赖。搜索由真实查询、向量和邻接行驱动，复用 V04-1a 的选择、去重、合并和固定维序 FP64 L2。FP32 输入、距离及 ID 排序沿用现有规则。

## 运行

```sh
cmake -S . -B build/native -DNDPSIM_NATIVE_ONLY=ON -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build build/native -j2
python3 tools/run_native.py --binary build/native/ndpsim_native \
  --config /path/to/native-config.json --data /path/to/dataset \
  --output results/native/run1 --workers 1 --concurrency 1
```

配置使用现行 `ndpsim-experiment-v2` 的 `format`、`index`、`truth` 和 `search`。后者必填整数 `entry/k/width/batch/budget/chunk`。索引与真值路径相对于配置文件目录。只读取这四项配置，原生 worker 和接纳上限由命令行指定。支持 1、2、4 worker，接纳上限至少为 worker 数。输出目录须为新目录。标准入口复用 dataset-v2、index-v1、truth-v2 校验器，原生进程再对其实际读入的五份 payload 检查长度和 SHA256。失败退出非零，进程日志和运行清单保留。

## 驻留与计时

完整向量、图、查询、真值 ID、原始查询 ID 读入进程自有字节数组，SHA256 顺序访问全量字节。邻接边界检查和每个 worker 的一次真实搜索预热都在开闸前完成。查询按当前决策的 ID 取向量或邻接行，每个 worker 持有完整查询并串行执行评分分块。真值仅在窗口结束后计算 Recall。

批次开闸时供给全部查询，首批按接纳上限入队，随后每完成一条按输入顺序接纳一条。查询 latency 从接纳到结果发布，另报供给到接纳和接纳到 worker 开始的等待。批次窗口从首批接纳到最后发布，P50/P95 用 nearest-rank。线程 CPU 计时覆盖每条同步搜索，含按 ID 取数、复制、解码、状态维护和结果形成，调度与输出另在该区间之外。worker CPU 占用按线程 CPU 总量除以 worker 数与批次窗口的乘积。批次 JSON 同时报时钟、分辨率、逐查询 top-k、距离、Recall、扩展/评分与七项逻辑 Work。

窗口内采集进程级 minor/major fault 增量，major fault 非零标为分页条件不适用。清单保存 macOS 进程运行前后的系统内存压力快照及子进程峰值 RSS。零 major fault 只表示这次窗口的进程计数，内存未锁页，仍受系统换页和调度影响。频率计数 Unsupported，核绑定未控制。RSS 预算在子进程退出后核对。

## 实测与证据

`results/v0.4/native-v041b/run1/` 保留命令、日志、输入/构建/源码/依赖身份、断言和 SHA256。独立构建源目录只有根 CMake 与 `src/`，配置、编译和运行成功，编译图和链接中无 SST/MQSim、Runtime 或 HostCpu，`otool -L` 只有系统 libc++ 和 libSystem。独立小夹具覆盖 4 种 worker/接纳配置与 5 个失败路径，逐查询核对 top-k、距离、Recall、Work、FIFO 与每 worker 串行。根配置、构建及 CTest 85/85 通过。

重查锁定输入 hash、RAM、磁盘和超时预算后，使用完整 SIFT1M base、NSG 和 tuning32，固定 k=10、L/E=128/256、B=1、chunk=16，各跑一次。三组 32 条查询的结果、Recall、扩展、评分及 Work 一致。

| Worker / 接纳上限 | 完成 | QPS | Minor / major faults |
| --- | ---: | ---: | ---: |
| 1 / 1 | 32 | 12.21 | 23 / 0 |
| 2 / 32 | 32 | 22.11 | 37 / 0 |
| 4 / 32 | 32 | 37.78 | 45 / 0 |

AC-04-01 已有独立构建和真实运行证据。上述 SIFT 数据是功能试跑，样本每配置一次，接纳窗口随配置变化。三路径语义、独立 evaluation128、Profiler、静态 IR、CPU 精度和常规 14 配置验收属于后续任务。完整继承 AC 与 52 条深度回归本轮未执行。
