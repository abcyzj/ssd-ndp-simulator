# T1 / M1 数据与 schema 收口

2026-10-30（Asia/Shanghai）。T0 数据身份前置已核对：M0 内容锁和本机完整 SIFT 源文件可用；未修改子模块或依赖。T1 完成，M1 整体尚未完成；本轮不启动 T2。

实现：`tools/benchmark_data.py` 提供四种格式的流式校验、原字节转换、显式/seed 选行、原始行号映射、独立 base/query/learn/groundtruth 角色及 dataset-v2 schema 检查。输出临时目录校验后改名，拒绝覆盖；原始完整哈希必须匹配 spec 声明。`ann_data.digest` 改为增量哈希；v1 生成/读取语义保留。`run_graph` 显式检查并拒绝尚未接线的 v2 准备产物。接口与例子见 [数据说明](data-v2.md)。

实际检查（均退出 0，日志在本机忽略目录 `results/v0.2-t1/`）：

- `cmake -S . -B build/delivery -DBUILD_TESTING=ON`，`configure.log`；复用 SST 缓存，不是干净重建。
- `cmake --build build/delivery -j2`，`build.log`。
- `ctest --test-dir build/delivery --output-on-failure`，最终 54/54，`ctest.log`。
- `PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_benchmark_data.py`，3 类测试通过，`unit.log`。覆盖四格式精确字节及顺序、截断/多余字节/坏头、NaN/非法 ID、未知版本/度量、错哈希/映射/维度、重复/负/越界选行、base 子集官方真值拒绝、v1 兼容。CTest 注册相同测试。
- 锁定 Python 直接调用 `convert/check`：输入 M0 锁定完整 SIFT base=1M、query=10k，完整扫描并核对哈希，选 base 原始 IDs `[3,0,2]`、query `[32,40]`；输出 shape=3×128、2×128，检查通过。spec、源/转换后哈希和映射在 `sift-spec.json`、`sift-small/dataset.json`，输出在 `real-import.log`。可复跑：`python3.13 tools/benchmark_data.py import results/v0.2-t1/sift-spec.json --output <新目录>`，再 `check <新目录>`。

初次系统 Python `py_compile` 因默认 pycache 指向沙箱外被拒绝，未计通过；后续使用锁定解释器并禁用字节码写入执行测试。

限制：仅完成 AC-02-01 和 AC-02-02 的 T1 部分；index/truth/experiment schema 的绑定和独立真值归 T2/T3，未标整个 AC-02-02 或 M1 完成。转换后的 groundtruth 是整数 ID 数据，不是可直接模拟的真值产物。Deep 仅小格式夹具覆盖，未转换完整 10M；未执行完整 V0.1 AC、原生 oracle、ASan、规模资源验收或两放置真实数据查询。参数校准与硬件性能结论无变化。查询显式列表的内存随选择数量增长；base 全量默认 range 不展开 ID 列表。原始源需在转换期间保持不变；输出哈希用于一致性检查，不是签名或来源认证。
