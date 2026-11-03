# V03-6 完整矩阵与效率门槛

2026-11-03（Asia/Shanghai）。本轮仅处理 V03-6，不开始 V03-7，不切换默认 engine。

## 实现与复现

`tools/verify_ir_matrix.py` 使用锁定 Python；要求新输出目录，不复用历史仿真结果。入口复核 V03-0～5 成功 manifest 和登记产物、冻结预算、RAM/卷余量、完整原始文件/准备文件/图/真值/查询及 builder 身份，根构建、全 CTest 和定向 IR ASan 后执行固定矩阵。所有输入与冻结准备 manifest 精确匹配，不重抽查询或重建图。

完整 base 为 1M×128；两个 engine 各26条，包括32/128查询四点扫描、128评价主配置重复、并发2/4、detail8 的 statistics/detail。评价主配置的原始和重复条目作为前两组，再补第三组；各 placement 的实际顺序为 direct/ir ×3。Q 梯度使用固定 N2048/d32/R16/L32/E64/chunk16/c1、Q32/128/512、两 placement 各3次，共18次 IR 运行；生成一次Q512，再复制同一 base/图并裁取嵌套查询前缀和独立真值。每样本记录必要查询输入及终态结果文件大小，不把序列化文件大小当私有堆测量。

产物包括 `schedule.json`、每点配置/命令/log/退出码/hash、`scan.json`、`quality.json`、`quality-matched.json`、各点严格等价报告、两 engine 独立 detail 账本、`efficiency.json`、`gradient.json`、`disk-measurement.json` 和失败关闭 manifest。中途失败/缺点/预算不通过均不能形成成功出口；不中途缩小矩阵或放宽阈值。

## 效率修复与不变边界

首个完整 host 诊断对照模拟严格等价，但旧 IR 查询114.08s/direct44.43s（单组2.57倍，不能冒充三组中位数）。保留旧诊断与失败 manifest 后修复：

- 每 Executor 的 `SubmissionValidator` 一次完整校验不可变 contract；只缓存静态 JSON 和活动 producer 的 ID/scope/output 描述，逐项完整校验新操作结构及原语义/引用。接纳临时数组在成功和失败后均清空；descriptor 随活动 record 回收。静态文件入口仍完整校验全文；目录摘要针对不可变嵌入目录只计算一次。
- Value 使用共享不可变 payload；评分保留原 packed uint32/FP64记录，工厂先完整校验记录和有限数值，发布前仍检查真实类型、shape和地址批次长度。功能服务继续调用原候选/选择/去重函数和 Work。`data()` 是延迟诊断视图，格式/数值与原 JSON 相同；它不提前授权读取或创建新服务。
- 严格比较器以标准 JSON decoder 流式读取 statistics 的资源数组，落入临时 SQLite，逐行按原顺序规范化/比较。保留嵌套 transaction 字段及未知字段；新增重复key/截断/尾随内容拒绝。比较键、白名单和模拟量判据不变；比较器仍保留必要ID关联，不宣称任意输入下恒定RSS。

SST、MQSim、Runtime、EventBridge、计费公式和原 direct 搜索均未修改。仍为 configured_unvalidated；无普通内部 transaction/共享 NAND participant 新归因。

## 实际检查与结果

正式入口实际执行：

```sh
/usr/sbin/sysctl -n hw.memsize > /private/tmp/v036-ram.txt
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_ir_matrix.py \
  --ram-evidence /private/tmp/v036-ram.txt --output results/v0.3/matrix-v036/final5
```

复跑必须换新输出目录；`--build` 默认 `build/delivery`，`--asan-build` 默认 `build/v036-asan`。RAM stdout 是受限沙箱下单独读取的本轮实测16GiB，不沿用历史RAM快照。

主证据 `results/v0.3/matrix-v036/final5/manifest.json`：**passed=true，52/52，171项断言全true，129个命令阶段全部退出0，2,831项登记产物**。manifest SHA256 为 `82b6f200a6f92b1893e3d690f7ffed58dc34a536515876d52879144172356c41`。实际共78次生产SST新运行：52条矩阵、4次第三组效率补测、4次冻结smoke锚点、18次Q梯度；没有复用历史仿真结果。

- 根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON -DNDPSIM_SLOW_FUNCTIONAL=OFF`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure` 均退出0，**80/80**。新增 `ir_matrix_gates` 五组检查覆盖52条唯一计划/交替次序、缺项和墙钟/RSS超限拒绝、Q泄漏/增长拒绝、固定图/嵌套前缀/真值、流式JSON跨块/重复key/截断/尾随内容。原19项比较器对抗和真实IR日志预算拒绝均随CTest重跑通过；新增C++接纳拒绝和不可变/packed值检查也通过。
- 定向ASan：根CMake配置 `-DNDPSIM_SANITIZE=ON`，构建 `ir_executor_probe ir_schema_probe ir_frontend_probe ir_frontend_causal_probe`；`ASAN_OPTIONS=halt_on_error=1 ctest --test-dir build/v036-asan --output-on-failure -R '^(ir_dynamic_lifecycle|ir_schema_memory|ir_graph_frontend|ir_graph_frontend_causality)$'`：**4/4**，退出0。
- 28项同placement direct/IR比较（26条计划配对加2条第三组补测）通过；两engine各自重复、statistics/detail和并发1/2/4查询决策一致。四个detail条目提供完整关联；两engine的 `compare_placements.py` 独立重算真实FP64结果、逐批选择、请求/传输/credit和向量零D2H，均退出0。独立核对器峰值分别265,551,872/260,784,128B，最高预算engine比较器204,718,080B，均低于512MiB。
- 全部运行SST峰值最大854,425,600B（约0.796GiB），低于4GiB；初始化/查询/后处理、Python父进程与逐点资源门槛全部通过。盘控终态保留两个必要的ID非复用压缩区间，非活动task；活动task/lease/waiter/history及credit均清零。
- `build-proof/command.json` 退出0，补采完整SST源码、实际element/SST可执行文件、CMakeCache、编译flags和SST缓存receipt身份。`closeout-analysis.py` 由本轮主manifest登记；锁定Python以 `final5` 和新目录 `results/v0.3/matrix-v036/closeout` 为参数运行，退出0并重新核验全部主证据hash。补充 `closeout/manifest.json`、`frozen-full-checks.json` 证明两placement完整评价集的新direct结果和全部模拟statistics与V03-0冻结参考相同；IR经同placement严格比较关联到该参考。补充命令receipt/log亦保留。
- 运行期间源码/未提交diff、MQSim和SST完整身份、冻结预算均未变；收口后只更新说明和任务状态。

三组全部统计模式样本（秒）与中位门槛：

| placement | direct查询墙钟，按实际顺序 | IR查询墙钟，按实际顺序 | 中位IR/direct | 最大配对RSS增量 | 结论 |
| --- | --- | --- | ---: | ---: | --- |
| host | 42.51004, 42.86324, 43.22682 | 55.72073, 55.15012, 55.79151 | 1.299966 | 2.125MiB | 通过 |
| controller | 21.47379, 21.60903, 21.93793 | 30.23389, 30.85690, 30.93711 | 1.427963 | 20.562MiB | 通过 |

阈值仍为运行前冻结的1.5倍、每对 `max(128MiB,20%×direct峰值)`，没有倒填或放宽。

Q32/128/512各3次、两placement共18个样本：终态活动op/value和旧历史映射均清零，峰值始终为5 ops /7 value slots，依赖描述随活动record回收。必要查询输入为4,096/16,384/65,536B，终态结果文件逐样本登记；不把文件字节解释成私有堆。

| placement | Q32 SST峰值中位B | Q512 SST峰值中位B | 增量B | 门槛B | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| host | 35553280 | 35635200 | 81920 | 67108864 | 通过 |
| controller | 36765696 | 53395456 | 16629760 | 67108864 | 通过 |

磁盘口径含原始SIFT、共享准备输入、全部V0.3证据（包括开发失败）：3,819次采样峰值 **10,764,730,877B**（约10.025GiB）<25GiB；最小卷余量 **83,994,238,976B** >20GiB。

四点质量报告（两engine、两placement结果一致）：

| L/E | tuning32 Recall@10 | evaluation128 Recall@10 |
| --- | ---: | ---: |
| 32/64 | 0.953125 | 0.9484375 |
| 64/128 | 0.98125 | 0.98984375 |
| 128/256 | 0.996875 | 0.9984375 |
| 256/512 | 1.0 | 0.99921875 |

图覆盖/入口可达性、原builder质量及最高预算项目Recall≥0.90门槛通过。`quality-matched.json` 仍只在调参集选择：0.95目标选出的32/64在独立评价为0.9484375，**目标未达到**，没有用评价点重新选参。这不等于固定最高预算质量门槛失败。

`closeout/detail-overhead.json` 分列detail8的统计/详细模式查询墙钟及SST RSS：IR-host为4.23992→6.46963s（1.526倍），IR-controller为2.33404→4.33038s（1.855倍）。这是详细诊断开销，单独保留，不作为统计模式1.5倍门槛的样本。

开发/失败证据保留：`final1/2/3` 在进入生产仿真前中断，分别修正“压缩协议非复用区间必须为零”的错误断言、补齐流式比较和冻结输入身份；`final4` 只完成首组完整对照即因旧IR开销进入优化，不能算52条通过。临时计时插桩曾向schema工具stderr追加PROFILE，导致一次CTest 79/80（JSON诊断解析失败）；移除插桩后以正式源码重新构建并80/80通过，未弱化断言。只有final5作为本轮完整出口；开发样本不计入正式三组。

V03-6出口及AC-03-09满足，Q梯度补齐AC-03-07对应部分；AC-03-10的正式默认切换与其余交付项目仍属于V03-7，未标整体通过。


## 限制

完整 V0.1 AC、原生 oracle、V03-7 总验收和默认切换不在本轮。ASan 仅定向 IR 夹具，不是完整 SST/上游泄漏审计。128/32/8为锁定查询子集；p99不作稳定尾延迟证据。官方ID-only top100严格FP64并列边界仍 unresolved。RSS为整个SST/父进程高水位，不支持分阶段/私有/同时进程树RSS；磁盘为1s逻辑大小采样，可能错过瞬时峰值；复用锁定SST缓存，不代表干净重建。
