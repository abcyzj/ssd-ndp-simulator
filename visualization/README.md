# NDPSim V0.4.5 可视化

使用 Python 3.11 或更新版本。运行时只需 Python 标准库，安装包内置前端产物。打开导出的 DAG 无需网络。时序可用 Perfetto 查看。

从仓库根目录安装。

```sh
python3 -m pip install ./visualization
ndpsim-viz --help
```

## 导出

```sh
# 仅采集包
ndpsim-viz --package "$teaching_package" --query 0 --output teaching-viz

# 单次运行
ndpsim-viz --package "$formal_package" --query 2579 \
  --report "$host_report" --output host-viz

# 同包、同原始 query 的 Host/Controller 对比
ndpsim-viz --package "$formal_package" --query 2579 \
  --report "$host_report" --report "$controller_report" --output comparison-viz
```

`--query` 使用采集时的原始十进制 ID。运行报告使用 `ndpsim-run-report/1` 的 `report.json`，保留相邻的原始 trace 和 CSV。输出目录必须是新目录，且在输入目录之外。SQLite 索引存于输出的 `cache/`。失败返回 2，可能留下缓存用于诊断。以 `manifest.json` 为导出完成标志。

- `dag.html` 支持按算子搜索、展开阶段、检查依赖及复制阶段键。
- `timeline.json` 可导入 Perfetto。Capture-only 模式的时序为空。双运行按各自 admission 对齐并独立分轨。
- `manifest.json` 记录包、报告及明细的内容身份、轨道覆盖、时间偏移、验证状态及输出文件散列。

## 在 Perfetto 查找同一阶段

打开 DAG，展开算子，选择阶段并复制阶段键。在 Perfetto 导入 `timeline.json` 后打开 SQL 查询界面，将下方字符串替换为该键。查询结果按 Host 和 Controller 进程分别显示，选择结果可定位切片。

```sql
SELECT p.name AS run, s.id, s.ts, s.dur
FROM slice s JOIN thread_track tr ON s.track_id=tr.id
JOIN thread th USING (utid) JOIN process p USING (upid)
WHERE s.category = 'Stage'
  AND EXTRACT_ARG(s.arg_set_id, 'args.stage_key') =
      '{"query":"2579","op":"op2","stage":"score_l2.1"}';
```

源 SST 纳秒和相对纳秒保存在事件参数中。显示时间如有共同偏移，可从 manifest 查阅。阶段区间覆盖输入、服务及必要返回。缺关联键的资源记录按时间窗口展示，未观测的依赖 ready 等待及资源端点标为 Unsupported。SSD、PCIe 和 Controller 延续 `configured_unvalidated`。详见 [数据契约](DATA-CONTRACT.md)。

## 本地验证

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=visualization/src \
  python3 -m unittest discover -s visualization/tests -v
node --test visualization/web/graph.test.js
(cd visualization/web && npm ci && npm run build)
```
