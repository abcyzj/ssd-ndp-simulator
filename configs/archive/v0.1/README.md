# V0.1 阶段配置归档

保留 M3.1 主机 smoke 和 M3.4 两放置 smoke 配置的原始参数（包含旧的 event_log=true）。它们用于[归档阶段脚本](../../../tools/archive/v0.1/README.md)复现；低层回归入口 run_graph.py / run_pair.py 的无 --config 默认值仍引用相应 host smoke，以保留原 CLI 行为。当前日常运行器 run.py / run_experiment.py 不默认使用这些配置。日常实验使用 [configs/experiments](../../experiments/host.json)，持续回归夹具见[配置索引](../../README.md)。

- [M3.1 host](m3.1/host.json)
- [M3.4 host](m3.4/host.json)
- [M3.4 controller](m3.4/controller.json)

原路径分别为 configs/m3.1/host.json、configs/m3.4/host.json 和 configs/m3.4/controller.json；JSON 内容逐字节保留。仍被 CTest/完整 AC 使用的探针与分支夹具没有归入此处，而在 configs/regression/ 持续维护。
