# V04 Perfetto 时序验证

用正式 P19 B01/C01 的同一个 Capture IR/2 包和原始 query 2579。输入路径、身份及完整散列见 [V01 样本登记](v01-samples.json)。定向夹具含交叠区间、同刻端点、零时长、缺阶段和缺 PCIe 端点。

`write_timeline(path, package, query_id, runs)` 接受标注过的 `(label, Run)` 对并返回覆盖信息。每个运行单独显示，按各自 admission 对齐。源 SST 纳秒和相对纳秒保存在事件参数中。若被观察事件早于 admission，两个运行应用同一个非负显示偏移。Chrome JSON 的坐标以微秒表示。相交区间分配到独立轨道，同刻的切片和完成点也分别保留。

资源归因沿用数据契约。CPU、Controller 计算、Controller 的可解码缓冲提交以及 SSD 本地读完成点可携带阶段键。缺少完整阶段键的 PCIe 和 SSD 记录用独立的窗口轨道，协议记录按 query 展示。对横跨窗口边界的 PCIe 包保留已发出的全部端点。单个包缺实际端点时，只显示观察到的时间点。依赖 ready 等待、SSD 排队与服务、Controller 缓冲排队与服务标为 Unsupported。

## 复现

使用 [Perfetto 官方 Trace Processor](https://perfetto.dev/docs/analysis/trace-processor) 导入并查询切片。运行以下命令，`trace_processor` 的路径由调用方提供。它只用于独立核查。

```sh
export PYTHONDONTWRITEBYTECODE=1
PYTHONPATH=visualization/src python3.13 -m unittest discover -s visualization/tests -v
out=$(mktemp -d)
PYTHONPATH=visualization/src:visualization/tests \
  python3.13 visualization/evidence/check_v04.py "$out" /absolute/path/to/trace_processor
git diff --check
```

用 Perfetto SQL 查找从 DAG 复制的阶段键。以下查询返回同一阶段在两个运行的切片端点。

```sql
SELECT p.name AS run, s.ts, s.dur
FROM slice s JOIN thread_track tr ON s.track_id=tr.id
JOIN thread th USING (utid) JOIN process p USING (upid)
WHERE s.category = 'Stage'
  AND EXTRACT_ARG(s.arg_set_id, 'args.stage_key') =
      '{"query":"2579","op":"op2","stage":"score_l2.1"}';
```

## 实测结果

Python 3.13.12 测试 13/13 通过，独立核查和 `git diff --check` 均退出 0。使用 Perfetto v59.1-1e9c61e98，夹具导入 14 个切片，三个阶段的坐标分别为 3/4、4/5、7/0 ns（起点／时长），位于三条独立轨道。正式样本导入 29,103 个切片，B/C 两侧共 1,214 个阶段逐键与源 CSV 的起点、时长逐纳秒相等。独立 V01 证据中的每侧首包 PCIe 三端点及按类资源记录计数一致。输出的散列、字节数和覆盖见 [V04 机器记录](v04-timeline.json)。

首次验证脚本读取 Perfetto 表格输出时受终端模式影响。切换到 CSV 后，带引号的 JSON 阶段键仍无法直接用 CSV 解析器无损读取。最终 SQL 查询以十六进制返回阶段键，再还原原字符串逐个比对。导出端未为此改变阶段键表示或时间戳。
