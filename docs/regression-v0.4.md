# V04-5b 兼容与继承回归

2026-11-05 完成 [PRD §8.1、§8.3、§10 和 AC-04-11](prd-v0.4.md)、[R01～R04](validation-matrix-v0.4.md)及本轮继承出口。正式证据为本机 results/v0.4/regression-v045b/run2/，入口为 tools/verify_v04_regression.py。本任务只修改验收工具、门槛测试及文档。

## 本轮执行

使用锁定 Python 和根构建入口。完整命令如下，输出目录必须不存在。

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 -B tools/verify_v04_regression.py \
  --build build/delivery --output NEW_OUTPUT_DIRECTORY
```

入口执行完整输入与历史参考核验、根配置/构建/CTest、四次默认 small 运行、四个完整规模 R 点及四次 detail8 伴随运行、两 engine 的独立 detail8 数值/搜索/账本核对、完整 V0.1 AC、T3/T4/T5、IR ASan 和静态回放 ASan。所有模拟均在本轮新目录运行，历史输出只作比较参考。

R01/R02 显式 direct，R03/R04 在输入配置中省略 engine，实际配置、manifest、observation 和 SST 参数均记录 ir。四点均省略新增 Profile、provider 和 worker 策略，继续使用 SSD 和旧公式成本。完整 base 为 1,000,000×128，使用固定 evaluation128、锁定 NSG 图、L/E=64/128、k=10、B=1、chunk=16、单接纳槽。

| 配置 | placement / 实际 engine | 查询数 | 批次窗口 ns | Host 服务数 | 冻结结果与统计 |
| --- | --- | ---: | ---: | ---: | --- |
| R01 | Host / direct | 128 | 25,241,378,798 | 45,593 | 严格一致 |
| R02 | Controller / direct | 128 | 17,851,861,276 | 26,715 | 严格一致 |
| R03 | Host / ir，省略 engine | 128 | 25,241,378,798 | 45,593 | 严格一致 |
| R04 | Controller / ir，省略 engine | 128 | 17,851,861,276 | 26,715 | 严格一致 |

每个 R 点单独验证了真实查询 ID、预装载 2,000,000 sectors、初态排空、时间窗口、普通和本地流量、向量零 D2H、credit、会话/句柄/任务回收及资源预算。四个 detail8 的结果和时间与各自全量运行前八条查询严格一致，direct/IR 由正式比较器按原有类型语义键核对。

## 冻结参考与独立性

重新核验 V04-0 开工参考、V03-0 direct 冻结、V03-6 原始矩阵和 V04-1c 参考语义的发布 manifest 锚点及全部登记产物 hash。原始源码、依赖、输入和参考身份分别保留。

- small 新 direct 结果、统计和完整事件流与 V03-0 冻结相同，并与 V04-0 重构前参考对照。
- 当前四个完整 R 点分别与 V03-6 中同 engine、同 placement、同 64/128 配置的冻结输出比较。结果完全相同，统计仅归零顶层 wall_seconds 后逐字节计算 hash。
- 当前同 placement 的 direct/IR 用 compare_engines.py 核对全部模拟资源和时间，沿用原有枚举白名单，没有增加字段豁免。
- V04-1c 的历史主配置为 128/256。该证据只作已核验的依赖参考，本轮主配置使用现行 64/128。
- detail8 两 engine 各由 compare_placements.py 独立重算固定维序 FP64 距离、逐批搜索、请求/事务、PCIe 封包、每块服务/credit、返回因果和 Close 排空。完整 128 查询的决定摘要及 Work/服务账本由全量结果与冻结统计对照承接。

历史矩阵的 hash 核验不计作本轮执行或深度回归通过。现有 52 条完整性断言及其工具保持原义。

## 继承检查

| 实际检查 | 结果 |
| --- | --- |
| 根 CMake 配置、-j2 构建、完整 CTest | 全部退出 0，92/92 |
| 完整 V0.1 AC-01～AC-11 | 11/11，完整模式 |
| 锁定原生 MQSim oracle | 独立 git archive 导出和编译，QD1/QD32/小 CMT 与 SST 各两次逐请求/初态严格对拍 |
| 快/慢 L2 功能变体 | 两种 placement 的完整事件、结果、模拟统计一致，慢变体实测更慢 |
| 继承 ASan | 17/17 |
| V0.2 T3 | 真实 SIFT 输入闭环、合法向量扰动及两放置独立核对通过 |
| V0.2 T4 | memory/mmap、设备容量、d96/d128/d260、跨页及两 placement 通过 |
| V0.2 T5 | 任务/lease/credit 回收、统计/详细等价、预算拒绝、小夹具 Q32/128/512 三次重复及真实 SIFT 核对通过 |
| IR ASan | 生命周期、schema、前端、因果、placement 映射 5/5 |
| 静态回放 ASan | 内存与 SSD、容量重分块、错误值/Work、旧成本门控、worker、重采和失败排空夹具通过 |
| 新门槛测试 | v04_regression_gates 的 5 组测试通过，覆盖缺项/失败、quick、重复 R 点、未知改动及深度触发阻塞 |

完整 V0.1 AC 另执行自己的根 CTest。相关 IR 因果/可见性、同刻仲裁、日志及资源断言继续保留在根 CTest 和上述定向测试。全部命令、环境覆盖、退出码、日志和子套件原始产物保存在 run2/。

## 深度回归判定

用户明确要求本轮不执行深度回归。入口没有调用 52 条矩阵、三组全规模交替效率或其配套全图 Q 梯度，报告显式保存 deep_regression_executed=false、disabled_by_user 和 not_run。T5 自身的 N256 小夹具 Q 梯度仍按该继承套件原标准执行。

regression-impact.json 对三项触发条件逐项判定。本轮均未触发，判为“本轮不要求，未执行”。

1. 默认语义/计费/事件/SSD 路径的改动或无法排除影响。已审查 V04-0 以来的纯核心提取、可选 Profile、PlatformOnly、静态接线与被动观测改动，原默认分支保留。冻结结果、全部模拟统计、定向和完整继承对照通过。
2. 无法解释的跨配置结果、时间、账本、确定性或资源异常。本轮全部 R 点及继承检查通过，未留下未解释异常。
3. 依赖、共享布局/设备模型或旧完整矩阵门槛改动。MQSim/SST 实际 commit、源码 hash 和相对原生 diff 与冻结身份一致，布局/硬件参数和旧 52 条门槛未修改。

新工具遇到未知默认源码改动、依赖改变、兼容检查失败或深度要求未满足时会拒绝通过，仍保持不执行深度回归。门槛测试覆盖此行为。

## 资源、失败记录与限制

运行前重新核验原始 SIFT、准备数据、图/真值、原始 query ID、builder 二进制/OpenMP/receipt 和真实 RAM。冻结本任务输出预算 8 GiB、卷余量至少 20 GiB、查询进程 4 GiB、后处理 512 MiB，保留全部既有证据。本轮新输出逻辑量采样峰值约 2.00 GiB，最小卷余量约 55.34 GiB。四个 R 点 SST 峰值 RSS 约 788～803 MiB，Python 后处理约 75～79 MiB，均在门槛内。

run1/ 保留验收工具预算字段拼写错误导致的失败。R01 模拟当时已成功，工具在资源核验处拒绝继续。修正后 run2/ 重新运行全部必需检查。该失败未计作成功配置。

使用缓存中的锁定 SST，未做操作系统级干净重建。ASan 为定向地址错误检查，静态 SST 夹具关闭 leak 检测，不能据此声明完整 SST/LSan 审计。RAM 和磁盘指标为进程高水位及 1 秒逻辑大小采样，未隔离私有堆或同时进程树峰值。硬件继续 configured_unvalidated，官方真值并列边界与固定查询子集限制保留。

AC-04-11 本轮通过。V0.4 总交付、全部 A/D/O 的最终身份关联和 AC-04-12 由 V04-5c 汇总。
