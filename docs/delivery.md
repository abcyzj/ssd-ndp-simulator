# 构建与可复建交付

现行交付流程；环境与依赖要求见[README](../README.md)及[源码维护约定](mqsim-source-management.md)。历史 M4.3 验收不自动证明后续修改已通过全量验收。

## V0.2 / SIFT1M 完整交付

T8/M4已完成，实际结果、全部适用AC、资源和首轮失败恢复见 [V0.2交付记录](releases/v0.2.md)。以下是当前总入口；后面的run.py方法保留为V0.1合成实验/干净构建基线，不代替SIFT1M交付。

在本机macOS/arm64、锁定子模块和预装工具可用的条件下，先按 [数据说明](data-v2.md)、[图与真值](index-truth-v2.md) 准备真实小SIFT的 `small-data/index/exact`，按 [T6入口](t6-sift1m.md) 准备完整SIFT1M的四组数据、同一NSG图和官方真值。原始SIFT四文件须与内容锁一致；不下载/初始化依赖，也不随Git附送数据或已忽略结果。

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_v02.py --all \
  --prepared results/v0.2-t6/preparation/prepared \
  --small-prepared results/v0.2-t2/real-final \
  --source-dir .cache/v0.2-m0/data/sift \
  --reuse-runs results/v0.2-t6/matrix \
  --build build/delivery --output results/v0.2-t8/new-delivery
```

输出必须不存在。`--reuse-runs`仅适用于已有26次成功放置；逐项验证输入/配置/源码/元素/依赖/输出和原命令receipt/log，再重新执行所有T6断言及独立详细核对；不匹配即拒绝。新检出或模型/输入变更时，省略此参数重跑26次矩阵，不能修改身份条件让旧结果通过。准备产物的完整数据/图/真值/查询锁也重新验证；总入口额外检查原始四文件和建图器身份。主配置完整base/评价128条的host/controller始终重新运行，完整结果和全部模拟统计对照矩阵，只有墙钟不参与统计相等性比较。

入口固定执行根配置/构建、完整V0.1 AC（含CTest、原生oracle、慢功能/ASan）、真实小数据内容扰动、两内容后端/profile与Q32/128/512三次资源复验。`--all`无quick替代；使用锁定Python并开启assertions。原生oracle从锁定原生commit导出，开发MQSim直接编译fork。每个阶段保留命令、退出码、日志、墙钟/RSS与实际断言；失败保留failed/incomplete manifest，成功才生成11项适用AC通过，AC-02-10=N/A。没有自动提交或下一个任务。

| V0.2输出 | 内容 |
| --- | --- |
| `manifest.json`、`assertions.json`、`ac-matrix.json` | 总体状态、阶段命令/退出码、完整产物哈希、适用AC与N/A |
| `source-start.json`、`source-end.json`、`dependencies.json` | 运行前后源码/依赖身份、原始SIFT与builder、历史收口追溯 |
| `v01/`、`t3/`、`t4/`、`t5/` | 本轮完整继承回归与小规模增量复验 |
| `sift/scan.json/csv`、`report.md`、`quality-matched.json`、`assertions.json` | 26点矩阵、质量/模拟性能/效率、质量匹配与全部断言 |
| `reproduce-host/`、`reproduce-controller/`、`reproduction.json` | 两放置新运行、top-k/距离/统计稳定性和预算 |
| `disk-measurement.json`、阶段`.json/.log` | 每秒磁盘逻辑大小/余量、wait4 RSS/墙钟，Unsupported口径显式记录 |

严格复用使用符号链接；迁移时须保留准备、原矩阵及引用目标，不能只拷贝汇总。许可证和子模块源码随源交付保留；构建下的原生独立导出及SST缓存回执也应随证据保全。运行时源清单不等于运行后文档编辑的字节身份，最终文档检查另记。模型未校准、官方真值并列边界、固定查询样本、RSS/磁盘采样等限制见 [交付记录](releases/v0.2.md) 和 [模型限制](model-limitations.md)。本轮复用已有SST缓存，不声称新的干净SST重建。

## 复建与运行

日常只运行实验使用 `python3 tools/run_experiment.py --build build/delivery --output results/experiment`。该入口复用已有构建，支持 `--mode pair|host|controller`、`--config`、`--data` 和 `--event-log`；两种模式共享数据，但不做一致性核对，也不触发编译、CTest、AC 或原生回归。具体参数和输出见 [README](../README.md)。下述 `run.py` 继续用于构建、测试与交付。

环境前提和一条命令见 [README](../README.md)。常规 `python3 tools/run.py` 默认从根 CMake 构建、运行全部 CTest、两放置共享相同数据并比较最终 top-k；JSONL 默认关闭。加 `--event-log` 时还调用独立离线核对器重放全部候选/扩展、容差、消息/存储/credit 账本。`--mode host/controller` 可分别运行；`--config` 和 `--data` 支持已有配置和数据。

本机干净交付验收命令（所有输出目录须不存在）：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/run.py --clean-build --build build/v0.1-clean --sst-cache .cache/v0.1-clean-sst --output results/v0.1-clean --event-log --verify-ac
```

`--clean-build` 要求 build、build-asan、build-slow、显式 SST cache 均不存在，不删除或覆盖；输出目录也必须不存在。`--verify-ac` 要求 Python 3.12+（原生 oracle 的安全归档解包）；文档固定用 Python 3.13。它调用实际 M4.2 完整矩阵，不接受 quick subset；独立导出/构建原生 oracle、编译慢变体、ASan 探针。初始 CTest 内有小矩阵，它不替代随后完整 AC。首次 SST 完整构建耗时较长，日志持续写入 configure.log 和 SST cache 下 logs。没有配置阶段超时；失败以非零退出，保留日志和 failed manifest，可换新目录重跑。默认根 CMake 的缓存流程不依赖已有安装或 M0.2 脚本。

统一入口不会执行 Git 初始化、提交、推送、下载或升级。依赖不全时用户须先提供主仓锁定的子模块；禁止用原生 MQSim 替代开发 fork。若 MQSim 存在未提交修改，manifest 会同时记录 HEAD、相对原生基线 binary diff SHA256 和逐文件实际源码 SHA256；哈希不是可应用的源码包，交接时仍须提供实际源码，见 [维护要求](mqsim-source-management.md)。

## 输出与交付清单

| 文件或目录 | 用途与检查 |
| --- | --- |
| `host/`、`controller/` | results.json（top-k/距离/独立 Recall）、statistics.json/CSV、queries.csv（接纳等待/模拟延迟）、top-k.csv、资源明细、capabilities.json |
| 两放置 `config.json`、`sst-params.json`、`layout.json`、`ssdconfig.xml`、`manifest.json` | 生效配置、逻辑布局、源码/输入/真值/产物哈希、依赖身份和模型假设 |
| `host/data/` | 固定 seed 的真实向量/索引/查询和独立 truth；盘控直接复用这份输入，位置写入 manifest |
| `events.jsonl`、`comparison.json` | 仅启用日志时输出；逐批语义与零完整向量 D2H/完整计费独立核对 |
| `ac/ac-matrix.json`、`ac/parameters.json`、`ac/manifest.json` | 完整 AC-01～11 的实际断言、输入/结果/命令、退出码和哈希；各证据路径相对 ac/ |
| 顶层 `manifest.json`、阶段 `.log`、`sst-build/` | 入口参数、macOS/架构/Python、构建/运行/验收命令及退出码、全部交付输入与产物哈希；依赖前后不变断言；bundled SST 的回执、命令和日志副本 |
| `licenses/` | MQSim/SST 原文、平台 MIT 许可证 |
| build 下 `sst-paths.json`、SST cache 下 `receipt.json/commands.json/logs/` | 实际 SST 安装位置、固定源码与工具链身份、Autotools 各阶段命令及日志 |

顶层 manifest 的 passed 表示本次选择的入口阶段通过；只有 `--verify-ac` 且完整矩阵通过时才声明全部 AC 重跑通过。常规运行不自动继承历史 AC 的通过身份。详细统计口径见 [M4.1](output.md)。配置不支持/非法参数、构建或断言失败均非零退出；不生成成功结论。

可评审源交付包括根 CMake、src/、tests/、configs/、tools/、deps.lock.json、.gitmodules/gitlink、docs/、AGENTS.md 及许可证通知；实际子模块源码和原始许可须一起可取得。docs/evidence、results、build、.cache 是忽略的本机产物；不能只交付主仓快照却遗漏 MQSim 实现。若要迁移本次证据，连同 SST 回执和原生导出目录一起保存，不能只引用将消失的临时路径。

## AC 追溯

| AC | 复现责任与本次输出 |
| --- | --- |
| 01 | 顶层两放置运行及配置/结果；ac/branch-*、default-* |
| 02 | ac/branch-*、mutated-*；仅改向量产生预期扩展/LBA |
| 03、04 | ac/causal-*.log、nand-*.log；真实 SST 可见性/后继与 NAND 扰动 |
| 05、06 | comparison.json、ac/*-comparison.json；逐批/距离/本地路径与完整流量账 |
| 07 | ac/concurrency/manifest.json、failure-*.log；容量、背压、接纳与安全回收 |
| 08 | ac/native-*.log、qd*-*.log、cmt-miss-*.log；锁定原生独立导出与普通路径逐请求回归 |
| 09 | ac/concurrency/；独立并集/守恒核对、分位样本、Unsupported |
| 10 | ac/concurrency/、fast-*、slow-*；重跑/日志/并发稳定，慢功能只改变墙钟 |
| 11 | ac/host-formula.log、link-formula.log、*-rate*/、parameters.json；隔离服务公式 |

每项具体可执行断言见 [M4.2 矩阵说明](testing.md)，历史 M4.3 结果见[归档](delivery.md)，本次核查见[交接](releases/v0.1.md)。扩展接口见 [扩展指南](extensions.md)，未覆盖能力与未校准参数见 [模型限制](model-limitations.md)。平台代码和文档使用 [MIT License](../LICENSE)，依赖许可材料完整保留；分发时须遵守平台及各第三方依赖的原有条款。
