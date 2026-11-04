# V04-2b 原生 Profiler 与静态 IR 采集

依据 [V0.4 PRD 的 P01～P07、N04、S07](prd-v0.4.md) 和 [V04-2a 冻结契约](contract-v0.4.md)。采集使用 [V04-1b 原生入口](native-v0.4.md)和 [V04-1c 参考语义](reference-semantics-v0.4.md)。下面的四档使用相同数据与搜索参数。

| `--mode` | 窗口与产物 |
| --- | --- |
| `uninstrumented` | 保留单调墙钟批次和查询窗口，跳过线程 CPU 探针 |
| `benchmark` | 在每查询搜索入口和出口记录线程 CPU，保留低干扰基准 |
| `cpu_sample` | 记录互斥线程 CPU 区间、空探针修正、低信号标记、评分外层闭合、覆盖与未归因 CPU |
| `static_capture` | 采集全部查询的实际读取、评分、合并、选择、去重、状态转换和终止；可附加 `--detail8` |

```sh
cmake -S . -B build/native -DNDPSIM_NATIVE_ONLY=ON
cmake --build build/native -j2
python3 tools/run_native.py --binary build/native/ndpsim_native \
  --config /path/to/native-config.json --data /path/to/dataset \
  --output results/native/capture1 --mode static_capture --detail8 \
  --output-budget 67108864 --query-buffer 4194304 --timeout 180
python3 tools/v04_contract.py package results/native/capture1/package \
  --binding results/native/capture1/binding.json
```

显式模式使用锁定 Python 的 `jsonschema` 环境，输出 `measurement.json`、校验报告和原生运行清单。省略模式走原有批次报告入口，保留 `--semantic-log`。显式模式的 `--split` 默认为 `fixture`，正式采集时由调用方指定并独立冻结原始 query ID 划分。

## 采样、封包与失败

CPU 采样使用 `CLOCK_THREAD_CPUTIME_ID`，`--sample-stride N` 按输入查询顺序和桶内调用次序，从第一个调用起每 N 个选一次。stride 大于 1 时，开闸前用同一搜索计算各查询的调用数前缀，运行后核对本次实际计数；每个 worker 只给选中的区间读钟。评分准备和 L2 共用采样选择及交界时戳。合并桶分列 chunk 拼接/工作集销毁与整批 rank 更新。resident_random 的 scored/expanded 插入分别由取向量及邻接准备区间覆盖。未归因报告按采样记录和查询线程 CPU 计算，stride 大于 1 时只报告未采样调用数以及未采样和桶外时间的合计；stride 1 可单独观察桶外 CPU。探针修正为负时保留原值和低信号标记。计时产物与静态包分开，采集和采样窗口仅用于诊断开销。

原生 worker 将同一查询的事件存入有界缓冲，按查询批量写入暂存分片。封包器逐行绑定查询内的四类依赖和状态版本，按查询生成索引和摘要。评分和合并的摘要基于实际 packed 字节，邻接摘要覆盖实际 degree 和有效 ID。每查询 FINISH 摘要与批次 top-k 核对，七项 Work 汇总与批次核对。封包器用输入清单和可执行文件构造独立 binding，调用冻结的离线预检器后原子发布 `package/`。detail8 仅在批次报告附加前八条查询的显式评分与扩展轨迹。包的查询内拓扑独立于采集时钟和 worker 分配。

超时、输入或构建身份不符、缓冲或产物预算超限、丢失、截断或校验失败均使入口退出非零。失败目录保留日志和暂存文件，只有成功的 `package/` 是已发布产物。子进程 RSS 以退出时的峰值核对。输出预算覆盖分片与发布产物，批次 stdout 在子进程退出后核对，失败日志及运行清单可用于诊断。当前 Measurement 单文件沿用契约的 16 MiB 上限；较大采样集需提高 stride 或分次采集。绑核与频率仍无受控测量。

## 证据边界

本机证据位于 `results/v0.4/profiler-v042b/run3/`，含命令、退出码、日志、配置、源码/依赖/二进制身份与文件哈希。根 CTest 88/88，原生独立构建和链接检查通过。新 CTest 使用独立数值/搜索/Work oracle 核对四档和逐算子摘要，覆盖 detail8、采样位置、缺失、截断、超预算和无发布断言。

SIFT10k 真实输入的功能试跑覆盖 32 条查询。四档逐查询结果、Recall、扩展/评分及 Work 一致，静态包含 9,818 个操作与 23,212 条依赖。单 worker 与两 worker 的操作流和索引逐字节相同。评分与选择逐批关联 V04-1c 已核对的真实搜索参考。stride 4 的 2,583 个采样位置和 stride 1 的 10,327 个位置均由静态操作流独立重建并核对；样本、低信号数、未归因、窗口和资源分项见各运行报告。限制缓冲至 100 字节的公开入口退出 1，未发布包。

本轮试跑每档一次，时延和 QPS 仅为诊断。完整 SIFT1M tuning32 与三组 A/B/A 的后续证据见 [V04-2c](training-v0.4.md)。冻结模型后的 evaluation128、仿真静态执行、CPU 精度、14 配置和完整继承 AC 分属其他出口。
