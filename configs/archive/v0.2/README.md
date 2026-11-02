# V0.2 历史配置

[m0_preload.py](m0_preload.py) 从 `configs/regression/m0_preload.py` 移入，参数字节保持不变。引用核查确认它仅由历史 [run_probes.py](../../../tools/archive/v0.2/run_probes.py) 调用，不是 CTest、正式实验或完整 AC 的输入。

这是布局预装载可行性探针，仍使用现有 `ndpsim.M0Preload` 组件。组件及构建注册保留；本配置不表示 V0.3 初始化方案。其他有效回归、设备 profile、查询/预算锁和 SIFT1M 矩阵保留在[现行配置目录](../../README.md)。
