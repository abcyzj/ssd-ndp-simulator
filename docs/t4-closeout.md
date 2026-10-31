# T4 / M2 内容与 profile 收口

2026-10-31（Asia/Shanghai）。已读取根 AGENTS、README、V0.2 PRD/实施计划、相关契约/接口/测试说明，并核对 T1 数据/schema 和 T3 真实小数据闭环收口。开工工作区干净；MQSim/SST 子模块未改，未自动提交、推送、升级或初始化依赖。本轮范围止于 T4；M2 整体及 T5～T8 不标完成。

实现：默认 POSIX 只读 mmap 内容后端，保留 memory 后端；按请求读取范围并裁去邻接 padding，公式地址替代 N 项 Placement map，SHA256 不再复制全文件。文件开始/结束 SHA256、inode/大小/纳秒 mtime/ctime 和运行器身份复核防止改变输入后输出成功。算法仍只持 Runtime/Handles 能力；普通 DMA/CQ、跨 extent/local 子事务、计算与返回可见性边界不变。v1 仿真前校验也改为分块解码，小离线 oracle/生成器保留。

新增单一受控 profile 描述/生成器，CMake 生成 XML、拓扑/partition IDs、page sectors、静态资源估算；运行器快照同源，并核对实际元素返回的 XML SHA256。native-small XML 字节不变。2/16 GiB 原始几何的实际 namespace 分别为 3,900,701 / 31,205,621 sectors，原有 native-small 为 121,897；各自越过原始容量、超过实际 namespace、资源预算不足均有拒绝测试。实际 namespace 足够分别承载 2,000,000 / 20,000,000 sectors 的目标布局，但未以完整目标布局运行查询或预装载。

盘控启动前复用唯一 `Controller::bounds`，checked arithmetic 检查布局与整页末址、最小前进块、消息长度和实际向量 sector 覆盖。d128/d96 在 16 KiB 缓冲、chunk16、并发上限4 时可接纳4条；d260 使用1536 B sector_bound，确定性 chunk9/块14032 B，接纳降额2。`compare_placements` 修正对跨查询全局交错顺序的假设：按查询保留内部顺序进行完整比较，保留逐请求因果与账本断言。

实际验证命令及最终结果：

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
ctest --test-dir build/delivery --output-on-failure
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_device_profiles.py
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_t4.py \
  --prepared results/v0.2-t2/real-final --build build/delivery \
  --output results/v0.2-t4/final-evidence
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_acceptance.py \
  --build build/delivery --output results/v0.2-t4/v01-verified
cmake --build build/delivery-asan --target mapped_content_probe -j2
ctest --test-dir build/delivery-asan -R '^mapped_content$' --output-on-failure
git diff --check
```

根配置/构建退出0，复用有效 SST 缓存，非干净 SST 重建；最终常规 CTest **64/64**，Python profile/有界 v1 校验通过；映射后端 ASan **1/1**。最终 T4 专项8项断言全部通过（16个放置运行）；完整 V0.1 **AC-01～AC-11 全部通过**，包括独立锁定原生 MQSim QD1/QD32/小 CMT 各两次 SST 对照、单独快/慢功能变体和 **17/17 ASan**。上列最终命令均退出0；不是完整 V0.2 规模验收。

T4 专项输入：三个受控 profile，小合成 N32/Q4/R8/d128、d96、d260，及复用 T2 原 ID 重排的 SIFT 10k、32条预留诊断 query、锁定 Faiss NSG 和匹配 FP64 真值。未使用正式调参/最终评价样本。所有两放置均由相同输入闭环驱动，不采用离线访问 trace。memory/mmap 比较逐字节事件流、全部结果与除 wall_seconds 外的完整模拟统计。独立核对器检查逐批距离/候选/扩展、普通请求/local NAND 子事务、向量 D2H=0、必要消息/SQ/CQ账本、credit 峰值有界及最终归零。

证据在本机忽略目录 `results/v0.2-t4/`：`configure-verified.log`、`build-verified.log`、`ctest-verified-final.log`、`profile-python.log`、`asan-mapped-{build,test}.log`、`final-evidence/{manifest,assertions,ac-matrix}.json`、每条命令/退出码/wait4 RSS/日志、所有输入/config/profile/layout、详细事件、结果/统计与独立 comparison；完整 V0.1 证据另在 `v01-verified/`。新检出须先按 T2 重建准备产物，再执行 T4，不附带本机生成证据。

保留失败过程，未计通过：初轮 mapped_content 发现 MAP_PRIVATE 可保留旧页，仅重算映射哈希不足，修复为同时检查文件元数据；专项工具首次有 config/measurement 同名碰撞，随后发现核对器对跨查询顺序的旧假设，并修正 d260 应降额2而非接纳4的错误测试预期。一次整理引入 EOF 空行使 diff-check 拒绝，CTest/完整验收因此退出失败；修复后重跑，最终证据使用上述新目录，不覆盖或继承失败 manifest。

限制：静态 `pages*256 + blocks*4096` 是 metadata 代理估算，不是 RSS 上界；不覆盖 memory 后端内容、搜索状态、历史事件或宿主页缓存。query 仍全量读入，mmap 驻留页仍可能随 N 增长；T5 的历史观测/句柄回收未实现。输入必须在运行期间保持不可变，身份复核不是并发截断安全协议。没有完整 SIFT1M/Deep10M 图+初始化+两放置矩阵，未实现快初始化；Deep 完整建图仍有本机资源门槛。参数均 configured_unvalidated，profile 容量变更会影响 FTL/CMT，不声称商品 SSD 校准、绝对性能准确或盘控一定更快。ASan 是指定探针，不是完整 SST/上游泄漏审计；wait4 RSS 非整进程树同时驻留总和，磁盘仅文件大小快照。

最终 T4 真实输入两放置阶段资源（单次，wait4 子进程峰值，macOS bytes 转 MiB）：

| 内容后端 | 墙钟 s | 峰值 RSS MiB | 退出码 |
| --- | --- | --- | --- |
| memory | 32.235 | 960.1 | 0 |
| mmap | 32.306 | 859.2 | 0 |

本轮数据不能证明大规模内存上界或速度收益，历史记录仍主导许多内存开销。最终专项证据目录文件大小快照为 305,945,532 B，非磁盘峰值。16 个放置运行的实现源码 SHA256 均已与当前工作区复核一致。
