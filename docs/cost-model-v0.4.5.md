# P05 通用模型与注册接口

日期：2026-11-06（Asia/Shanghai）。**P05 已完成；P06～P21 未开始，全部新 AC 仍待验收。** 依据 [PRD FR04/07](prd-v0.4.5.md)、[P01 §4](contract-v0.4.5.md#4-mapping模型和measurement)和[实施计划](implementation-plan-v0.4.5.md)。直接依赖 P01 的 design-final1/closeout1 manifest 与全部 19/12 个登记产物重新核验通过；P02 SDK 为本次接口复用组件，另行重构建及回归。

## 实现边界

新增 [C++17 模型库](../src/model/cost/cost_model.h)，根 CMake target `ndpsim::cost`。只依赖公共 capture 元数据/SHA256，以及现有 nlohmann JSON 头文件和公共 JSON 校验帮助函数；不链接功能算子、Runtime、HostCpu、Controller、SST 或 MQSim 库。JSON 头仍来自仓库现有 `third_party/sst-core/external`，没有新增／下载／复制第三方实现。根 native-only 分流在模型构建之前；P03 独立负载构建不受影响。

P01 的头文件草案在本任务细化为 `Registry`、`Profile`、`Input` 和 `Compile/Estimate` 回调：注册时提供公式 ID/版本、参数 schema 和编译函数，加载 Profile 时验证并编译成仅接收整数特征的服务函数。无动态脚本／插件加载、稳定 ABI 或排队事件接口。算子使用 SDK 的 `OperatorSpec/StageSpec` 声明；缺失／重复注册、未知版本、阶段、上下文或位置均报错。映射和调度接口留给其对应任务，未提前接线。

`Profile::parse(raw, expected_sha256, registry, target, allow_fixture)` 加载冻结的 `ndpsim-cost-profile/1`，验证原始字节 hash、封闭 schema、算子声明、Work/特征单位和范围、公式参数 schema hash、参数及整个声明域。重复 JSON 键、浮点或布尔 uint64、非有限数、未知字段、64MiB/64层限制、重复模型和占位生产身份失败关闭。模型参数 schema 仅支持本实现明确列出的子集，未知 keyword（包括扩展 schema 的 `$ref`）拒绝；不是完整 JSON Schema 引擎。

`Profile::demand(Input)` 只读取算子/版本、阶段、位置、context、Work、特征、目标和必要的表 binding，返回 `uint64_t` **ns 服务需求**。所有 Work 与额外特征必须匹配声明；模型使用的范围特征可来自 Work 或额外特征，同名冲突拒绝。所有额外特征必须声明适用范围，未参与公式的 Work 仍检查完整字段和单位。没有业务 payload、捕获时戳、真实算法回调或宿主墙钟。

目标使用完整 `id/hardware/os/memory_bytes/frequency/affinity` 身份匹配，不查询执行宿主、不要求模拟宿主等于测量机。预测规则保留 Measurement、原始训练 query ID 和 fit 报告身份；实测表保留独立 source 和完整 binding。来源证据的真实性、训练隔离及质量报告内容由 P06/P15 负责，hash 本身不是质量证明。

P19 增加 `source=configured`，沿用相同的公式、参数、Work 和特征域校验，另要求 `parameter_source` 说明参数出处。此来源与 Measurement、训练 query ID 和 fit 报告字段互斥。模拟报告分列配置参数、拟合预测和实测表；配置参数可以核对服务账本，不能用于驻留 CPU 精度验收。

## 公式与参数

特征向量顺序取 Profile 的 `feature_ranges` 顺序；参数 schema hash 为排序键、ASCII 转义、紧凑 JSON 加 LF 的 SHA256，可由 `Registry::parameter_schema_sha256(id)` 取得。公式与参数严格分开，未知公式不回退。

| 公式 | 参数与服务规则 |
| --- | --- |
| `constant/1` | `ns:uint64`，精确原样返回，含 0 和 UINT64_MAX。 |
| `nonnegative-linear/1` | `intercept_ns`、有序 `coefficients`，binary64 顺序求和后 ceil；有限非负、禁 fast-math/FMA，在整个声明范围的上界检查结果小于 2^64。 |
| `upper-bound-table/1` | `bins:[{upper:uint64,ns:uint64}]`，恰一个整数特征；闭上界严格递增且末项等于范围最大值，下一整数进入下一桶，无插值或默认桶。 |
| `log-gbdt/1` | `initial`、正 `rate`、`trees`；前序树节点为有限叶值或 `{feature,threshold,right}`，最多 180 树／深3，`<=` 走左，显式右子树；按树序求和，`max(1,ceil(exp(initial+rate*sum)))`，验证结构及保守域上界。 |
| `integer-rate/1` | `setup_ns:uint64`、`units_per_ns:uint64>0`、有序 `weights:uint64[]`；checked 整数乘加及 `setup+ceil(sum(weight*feature)/rate)`。这是现有 Host distance/control、Controller compute 和独立 buffer 公式的通用表达，未包括 MQSim 存储/链路服务。 |

实测逐调用表不同于 `upper-bound-table/1`：`source=measured-table` 按完整 `(query,op,stage)` 稳定键查找，并逐项比较全部 Work/特征和外层 binding（包、输入、语义、采集构建、目标、context、workers/slots/admission）。stage ID 已标识具体调用；不从相似调用、相邻段或模型估算补缺项。至少三个不同 Measurement hash；重复行、重复 artifact、缺项／身份不符拒绝。三次测量的资格、质量、覆盖及中位数制表属于 P06，本接口不假装从行内 ns 重新证明质量。

编译期扩展调用 `add_formula(id, parameter_schema_json, compile)`，编译函数负责参数跨字段及全域数值检查，返回纯服务函数；不改核心调度、内置公式或 SST/MQSim。[测试探针](../tests/cost_model_probe.cc)含通过公共接口注册的 `example-double/1`，参数 schema 包含 Unicode 描述并独立核对 hash。它是 P05 接口夹具，不是 P17 的真实采集／双映射教学交付。

## 实际检查和证据

最终证据：`results/v0.4.5/p05/run4/`，含命令/cwd/退出码、日志、依赖 gate、源码／构建／配置身份、迁移审计和产物 hash。`reproduce.py` 记录本轮实际命令；它是开发证据编排，不是新增用户 CLI。

- 根 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure`：**95/95 CTest 通过**。
- [独立模型测试](../tests/test_v045_cost_model.py)：**15/15，228 个 C++ 请求**通过。独立 Python 整数、Decimal 和显式分支计算期望 ns；覆盖四种冻结公式、整数速率、扩展注册、顺序 binary64 舍入、零值、UINT64_MAX、全域溢出、非法树、单位／范围／目标／表 binding 及原始 JSON 负例。
- 根 `NDPSIM_SANITIZE=ON` 下构建 `cost_model_probe`，定向 `ctest -R '^cost_model_v045$'`：**1/1，通过同一15项/228请求测试**。
- 重新独立配置／构建 SDK，**2/2 SDK CTest**；显式传入新 `build/p05-sdk/capture_fixture` 后 **P02 16/16**。另重跑 **P01 11/11、P00 3/3**。
- 根 `-DNDPSIM_NATIVE_ONLY=ON` 配置／完整构建通过；模型 probe 的 `otool -L` 只含系统 libc++/libSystem，模型库未定义符号检查无功能算子／调度／模拟后端引用。
- 迁移清单 **327 文件／95 CTest**，新增源码和测试逐项登记；`git diff --check` 通过。MQSim/SST commit、实际源码和 MQSim 相对原生 diff 重新记录且未变化，无提交、推送或依赖升级。

开发中的失败均保留：run1 最初在探针文件尚未创建时配置失败；早期实测表负例夹具把输入与表行引用到同一 Python 对象，修改输入也修改期望，已分别复制并重跑。run1 的额外 P02 检查默认读取旧 `build/p02-sdk/capture_fixture`，Unicode DEL 字符与当前源码不符；没有修改 P02 断言，重新构建并显式选择新夹具后16项通过。run2 为通过的中间身份；随后统一 Unicode schema hash 并把模型构建移到 native-only return 后，在新 run3 重跑全部检查；最终补充生产 Profile 拒绝 synthetic binding 的负例后，在 run4 重验最终身份。早期日志不冒充最终身份通过。

## 限制

本次仅关闭 P05 的接口和数值／拒绝出口；没有启动 P06，没有生成正式拟合／校准模型，也没有执行完整 SIFT 精度或新 AC。新库尚未接入模拟资源，零服务仍须由 P07/P08 按异步 phase 门控，不能直接同步放行。旧图 Profile/HostCpu/Controller 路径作为迁移参考暂存，由后续任务接线／清理，不能把旧通过算新平台完成。

Profile 当前整体驻留内存，单份上限64MiB，未承诺正式全量逐调用表可以放入该限额；P06 正式制表须先核验预算，超限失败关闭，若需分片必须显式扩展格式和加载策略。已验证 macOS arm64；GBDT 的 exp 跨平台逐位一致性未承诺，源码／编译器／二进制身份必须随运行保存。设备／链路／Controller 仍 configured_unvalidated，不推断下沉更快。
