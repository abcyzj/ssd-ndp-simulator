# V0.2 SIFT1M 交付复现

> 版本归档：本页保留 V0.2 总入口与证据复用规则，不是 V0.3 验收流程。当前开发入口见[文档索引](../../README.md)。

## V0.2 / SIFT1M 完整交付

T8/M4已完成，实际结果、全部适用AC、资源和首轮失败恢复见 [V0.2交付记录](../../releases/v0.2.md)。以下是 V0.2 总入口；[日常构建说明](../../delivery.md)中的 run.py 方法保留为V0.1合成实验/干净构建基线，不代替SIFT1M交付。

在本机macOS/arm64、锁定子模块和预装工具可用的条件下，先按 [数据说明](../../data-v2.md)、[图与真值](../../index-truth-v2.md) 准备真实小SIFT的 `small-data/index/exact`，按 [T6入口](../../sift1m.md) 准备完整SIFT1M的四组数据、同一NSG图和官方真值。原始SIFT四文件须与内容锁一致；不下载/初始化依赖，也不随Git附送数据或已忽略结果。

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

严格复用使用符号链接；迁移时须保留准备、原矩阵及引用目标，不能只拷贝汇总。许可证和子模块源码随源交付保留；构建下的原生独立导出及SST缓存回执也应随证据保全。运行时源清单不等于运行后文档编辑的字节身份，最终文档检查另记。模型未校准、官方真值并列边界、固定查询样本、RSS/磁盘采样等限制见 [交付记录](../../releases/v0.2.md) 和 [模型限制](../../model-limitations.md)。本轮复用已有SST缓存，不声称新的干净SST重建。

