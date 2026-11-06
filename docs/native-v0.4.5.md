# P03 独立图搜索构建

日期：2026-11-06（Asia/Shanghai）。**P03 已完成；P04～P21 未开始，全部新 AC 仍待验收。** 依据 [PRD FR-045-02](prd-v0.4.5.md)、[实施计划 P03](implementation-plan-v0.4.5.md)及源码核查 SA-01/02。直接依赖 P02 的 `run2/manifest.json` 通过状态、manifest hash 及登记的 **191 个产物 hash** 已重新核验，记录于本次 `p02-dependency.json`。P00/P01 为 P02 的已核验前序；未把其通过代替本次出口。

## 实现

- [独立工程与使用说明](../workloads/graph-search/README.md)、[CMake](../workloads/graph-search/CMakeLists.txt)：显式 `NDPSIM_SDK_ROOT` 源码接入或 `find_package(ndpsimCapture)` 安装接入，无隐式父工程依赖。生产只需 C++17/POSIX 和 SDK；Python 仅用于测试。
- `native`、`native_observation`、搜索核心及功能算子从根 `src/` **迁入**工作负载，不维护第二份算法实现；输入驻留加载也随原生程序迁入。FP32 输入、固定维序 FP64 L2、容器及七项 Work 不变，显式禁 fast-math/FMA。
- 抽出 `content/value.{h,cc}` 中必要的字节和错误类型；独立工程的 hash 适配显式调用 SDK。不携带 ContentStore、Layout、Handles、Runtime、SST/MQSim 或整套模拟 `src/`。
- 根 CMake 直接复用迁入的功能源码，根 ContentStore 通过最小头文件引用原有错误定义。根旧 SHA256 保留用于迁移参考；本次实际以两种 hash 路径读取同夹具并各自接受独立 Python oracle 核对。根生产模拟功能链接仍在，留待 P20 删除。
- 独立数值/搜索/Work oracle 与驱动迁入负载 `tests/`。根 Python 测试保留薄转发文件，维护既有 import 和 CTest 接线。同步现用 V0.4 验收工具的源码身份清单；历史发布证据不回写。
- 新增自身 `--help`，保留 benchmark、uninstrumented、cpu_sample 和 legacy static_capture 分离运行。构建指纹绑定工作负载源码、CMake 与实际链接 SDK archive；保留三行 `--build-info` 协议。运行证据另存二进制、实际 flags、编译/链接记录与 hash。
- 迁移审计扩大到 `workloads/`，逐文件维护清单和 include/引用扫描；没有按迁移删除有效断言。

## 实际检查

最终成功证据：`results/v0.4.5/p03/run2/`。`reproduce.py` 保存编排；`commands.json`、各命令 JSON/log 保存命令、cwd、退出码、耗时/RSS/超时；manifest 保存输入、期望、输出、构建、源码和依赖产物 hash。所有成功命令退出0；缺失/错误 SDK 两个负例预期退出1。

1. **隔离源码和安装 SDK 两种接入**：仅复制 `workloads/graph-search/` 和 `sdk/` 到 `/private/tmp/p03 graph isolated …/`，没有根 src、third_party、SST 安装、缓存或历史工具；从带空格路径独立 Release 配置、编译、运行。编译记录不引用仓库路径，不发现 SST/MQSim；SDK 安装消费无需 SDK 源目录作为编译输入。
2. 两种构建各自 **3/3 CTest 通过**：60组搜索逐阶段 packed 数值与七项 Work 独立核对；FP64 舍入/维序、有限极值和非法数值；11查询×4组 worker/admission 的 top-k/Recall/Work/计数/FIFO；四种原生模式的全部查询、CPU样本、原始shard及预算失败。SDK 自身 **2/2 CTest** 通过。
3. 两种二进制另各跑一次独立11查询 benchmark，持久保存五份输入、独立 oracle 期望和真实报告。`otool -L` 仅系统 libc++、libSystem；`nm -u` 无 ContentStore/Runtime/Handles/Layout/SST/MQSim 依赖；缺失和无效 SDK 配置失败关闭。
4. 根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure`，**93/93，27.46秒**。这是迁移回归，非 V0.4.5 正式 AC。
5. P00 门控 **3/3**、P01 设计检查 **11/11**、迁移审计 **311文件/93根CTest**、`git diff --check` 通过。运行期间源码与依赖前后完全一致；收口文档和根构建身份另存 `closeout1/`。

最初 `run1/` 的隔离测试均通过，但根测试转发文件生成时遗漏字符串结束引号，导致6项根测试失败；完整日志保留。修复后先单独运行60搜索与4组原生配置核对，再在全新 `run2/` 完整重验。没有覆盖或删除失败样本。

两次成功独立构建的 `compile_commands.json`、CMakeCache、生成身份头、所有库/flags/link行 hash 均保存。MQSim fork、锁定原生 commit、相对原生 diff 和 SST 实际源码沿 P02 未变，完整身份见 `dependencies.json`。没有提交、推送、升级或初始化 Git。

## 限制及后续边界

P03 关闭的是独立构建、运行及小夹具功能/计数出口。现有 static_capture 仍为**旧 raw shard 格式，不是新 IR/2 包**；完整图阶段特征/依赖、Measurement/2、封包和独立业务语义报告由 P04 实施，本次未开始。结构 SDK 可用不等于图采集已经接入；完整 AC-045-01 还依赖 P04/P18，AC-045-11 还依赖后续独立工具/发布流程。

仅验证当前 macOS arm64，未验证 Linux 或全新操作系统。隔离目录不含模拟源码，链接和编译依赖明确；这不是容器级文件系统封锁，也不声明本机没有任何模拟器安装。测试需本机已有 CMake/编译器/Python，未下载安装依赖。

未运行 SIFT1M 全规模、正式新包采集、A/B/A、重新拟合、CPU精度、SSD矩阵、独立MQSim正式对拍或12项总AC。P00记录的原锁定NSG/prepared缺口仍阻塞正式输入出口；不得以小夹具代替正式规模。原生构建身份已变化，旧 CPU 表/模型/精度不能直接继承。资源和完整输入须在后续正式运行前重新预检；设备/链路/Controller仍 `configured_unvalidated`。

本次任务到 P03 收口，没有启动 P04 或其他后续任务。
