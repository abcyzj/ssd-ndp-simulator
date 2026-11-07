# P20 旧路径清理与回归

依据 [V0.4.5 PRD §2、FR11、AC-045-10](prd-v0.4.5.md) 和 [实施计划 P20、§7](implementation-plan-v0.4.5.md)。P20 已完成。本机证据在 `results/v0.4.5/p20/`，`manifest.json` 汇总命令、身份与产物 SHA256。清理前 Git HEAD 为 `64e2ca3`；P16、P17、P18 和 P19 的通过 manifest 与登记产物在 `run2/dependency-gates.json` 中逐项复核。MQSim fork、相对原生 diff 和 SST 身份在 `boundary3/boundaries.json` 中保存。

## 路径处置

- 删除 direct、动态 IR、旧静态 Replay、带真实业务内容的模拟 Runtime、旧成本和消息实现及对应公开运行器。当前模拟模块只执行 IR/2 元数据计划。旧格式由 CLI 拒绝；迁移从业务侧重新采集完整 IR/2 开始。
- 根测试构建复用独立图搜索 CMake 工程及其数值／搜索／Work 核对。保留独立原生工程、数据准备和原生 raw 诊断；raw 诊断产物不能作为新模拟输入。
- 去掉生产命名设备默认值和生成头，改用显式外部 XML。测试夹具、普通 NVMe 对拍组件和低层验证仍在测试构建中。生产构建的链接命令及符号核对覆盖内存、图搜索 SSD 和教学扩展三个模块。
- 把旧本地读适配器的有效断言迁到 `MetadataLocalReads`。整份计划必须先通过范围检查，反序完成可聚合，local route 不返回向量 DMA，普通读仍能完成。请求／事务篡改、未知及重复完成必须失败，禁止发布成功或重新提交。
- [逐文件清单](migration-v0.4.5/files.json)保存 250 个当前文件及 168 个退役记录。[逐 CTest 清单](migration-v0.4.5/tests.json)保存 82 项当前测试及 66 项退役记录，列明替代断言对应的当前测试。历史文档保留，指向退役源码的链接固定到清理前提交；V0.4 按其发布记录在对应源码上复现。

## 实际检查

| 检查 | 结果 |
| --- | --- |
| `cmake -S . -B build/p20 -DBUILD_TESTING=ON`、`cmake --build build/p20 -j4`、`ctest --test-dir build/p20 --output-on-failure` | 全部退出 0，最终 82/82。包括旧引擎拒绝、真实 SST 输入／服务／返回门控和零时延、MQSim XML／Layout、配额、失败与排空、安装 CLI、独立 Validator 和教学扩展。 |
| `cmake -S . -B build/p20-product -DBUILD_TESTING=OFF` 及完整构建 | 退出 0。无旧 SST 图搜索组件或独立图搜索可执行文件。生产模块的 `nm -C`、`otool -L` 及链接命令未发现功能库或旧执行器。 |
| `NDPSIM_SANITIZE=ON` 定向构建及 CTest | AddressSanitizer 29/29，覆盖后端生命周期、Controller、元数据本地读、内存计划、图 SSD 和教学扩展。宿主 AppleClang 拒绝 `detect_leaks=1`，该失败轮保留。 |
| 只复制原生图工程和 SDK 后独立配置、构建和 CTest | 3/3，数值／搜索／Work、原生 batch 和原始观测。根 CTest 另执行完整 IR/2 合成采集。 |
| `tools/verify_mqsim.py --build build/p20 --output results/v0.4.5/p20/oracle1` | 退出 0。锁定原生独立导出与编译，QD1、QD32、small-CMT、改 XML 四组原生及真实 SST 各 2 次，共同字段严格一致。 |
| P00 清单与门控、P01 契约、引用／链接审计、`git diff --check` | 当前库存匹配；P00 4/4、P01 11/11，当前公开入口和活动代码无本轮删除引起的悬空引用。V0.1 归档中原先缺失的历史证据仍在审计记录中单列。 |

正式复验命令、退出码、输入和产物身份见 `formal2/` 和 `tuning1/`。复验复用哈希匹配的 P18 原生构建和冻结训练、采集、样本、表及无探针 benchmark。独立原生源码和 SDK 输入未变；所有待比较的 SST 运行均由清理后构建重新生成。模拟前重新读取 RAM、磁盘和预算。受限环境拒绝 `sysctl hw.memsize`，macOS CLI 预检改从 `memory_pressure -Q` 同时读取物理 RAM 和可用比例，读取失败仍拒绝运行。

首轮 A01 比对把 trace 第一行中的运行目录也纳入逐字节比较，因新旧路径不同而退出。该目录保留在 `formal1/`。重跑时对除有效配置首行外的全部记录逐字节比较，并用 Validator 单独核对配置、身份、事件及账本。

## 正式驻留复验

| 配置 | 延迟 P50 误差 | 延迟 P95 误差 | QPS 误差 | CPU 总服务误差 | 预测阶段最大中位／P90 误差 |
| --- | ---: | ---: | ---: | ---: | ---: |
| A01 | 0.626% | 0.496% | 0.718% | 0.770% | 3.965% / 10.900% |
| A02 | 0.603% | 0.348% | 0.523% | 0.536% | 3.431% / 8.919% |

每点两次新模拟，与三次身份匹配的独立原生 benchmark 对照。两点的参考情景账本通过，逐调用表按覆盖、质量、绑定和唯一计费单独核对。tuning32 两点的低信号零估计与参考情景各重新模拟并复核逐阶段账本，共四份通过。

## 正式 SSD 复验

| 配置 | 查询数 | 模拟 QPS | 完整向量 D2H 字节 |
| --- | ---: | ---: | ---: |
| B01 | 128 | 9.9460 | 73,426,944 |
| C01 | 128 | 15.0348 | 0 |
| B02 | 128 | 5.0117 | 121,077,248 |
| C02 | 128 | 6.9684 | 0 |

四个点均通过独立流量、服务、分块、credit 和排空核对。同一逻辑包和设备条件下，Controller 对 Host 的模拟吞吐比为 1.512（L/E=32/64）和 1.390（64/128）。两组各自保留独立账本与 CLI 比较。

## 适用边界

正式 CPU 精度限定既有完整 SIFT1M 图、冻结原始 query ID、当前 Mac 和单 worker Host 驻留。SSD、PCIe 和 Controller 保持 `configured_unvalidated`。ASan 未覆盖宿主的泄漏检测。P20 记录 FR11／AC-045-10 所需证据，V0.4.5 干净发布和全部 AC 聚合留在 P21。
