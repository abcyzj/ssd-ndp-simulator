# V03-1 schema 与能力目录

2026-11-03（Asia/Shanghai）。V03-1 完成 `ndpsim-ir/1` 的结构契约和静态校验；尚未实现动态 IR 执行、资源映射或 GraphSearch 接入，不代表 V0.3 验收通过。依赖 V03-0 已关闭，本轮重新核对冻结 manifest SHA256 与全部冻结产物 hash；没有重新运行完整 SIFT，也没有用历史通过标记替代本轮检查。

## 产物与使用

- [C++ 接口](../src/ir/schema.h)：Manifest、DataObject、Type/动态 shape、四类 ValueRef、Scope、端口、Operation 和四类依赖；`parse(Json)`、`encode(Document)`、`validate(Document)` 共用校验，合法输入与内存结构往返保持一致。核心位置、操作、类型和依赖为枚举；算子特有参数及身份元信息为严格 schema 约束的 JSON 记录。数据对象仅表示只读逻辑集合，不授予内容读取权限。
- [JSON Schema](../schemas/ndpsim-ir-v1.schema.json)：Draft 2020-12 结构约束。[生成器](../tools/generate_ir_schema.py) 同时生成 schema 和[能力目录](../schemas/ndpsim-ir-v1.capabilities.json)，`--check` 只读检查生成物是否过期；CMake 将两者嵌入校验器，无运行目录依赖。C++ 复用锁定 SST 源树现有 MIT nlohmann/json 头文件，不改动 SST。
- [合法样例](../examples/ir/graph-contract.json)：七个 version 1 算子的合成结构夹具，含 manifest、三个逻辑对象、静态入口与动态地址、状态引用和汇合；零 hash 是明确的合成占位身份，不能当作真实输入或模拟运行证据，也不能独立回放。
- [命令行校验器](../src/ir/validate.cc)：`ir_validate FILE` 输出 JSON 校验摘要；`--schema`、`--capabilities`、`--catalog-hash` 导出实际嵌入的结构与能力身份。错误非零退出，携带 code/path/query/IR op/相关 producer/cause/detail；静态校验没有模拟时间，`time_ns=null`。

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
build/delivery/ir_validate examples/ir/graph-contract.json
build/delivery/ir_validate --capabilities
ctest --test-dir build/delivery --output-on-failure
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_ir_schema.py --validator build/delivery/ir_validate
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/generate_ir_schema.py --check
```

## 冻结规则

1. 顶层为 `manifest/objects/operations`。manifest 记录 IR、engine/mode、frontend、数据/图/真值/查询 shape/hash/ID 映射及顺序、搜索、映射/成本/初态、源码/依赖、诊断预算和算子版本。`catalog_sha256` 是目录 JSON 按键排序、无空白 UTF-8 编码的 SHA256（当前目录为 ASCII；与 C++ `Json::dump()` 一致），不对带缩进的文件字节求 hash。改动目录后须更新样例及相应 manifest。
2. 核心字段严格封闭；只有 `extensions={non_semantic:true,metadata:{...}}` 允许未知元信息。未知版本、算子/版本/Kind 组合、位置、依赖及字段直接拒绝，不静默回退。保留类别不赋予 WRITE/PQ/任意分支等执行能力。
3. `shape` 的每一维为 `{extent:uint64,dynamic:bool}`；本版标量/状态/令牌为零维，集合/向量/得分/邻接为一维，动态 extent 表示上界，实际长度仍须在后续执行阶段检查。邻接 shape 计记录数，每条记录另有 degree 和有效 IDs；逻辑范围按字段元素计，不能携带 LBA/PPA。SCORE/READ 保持地址批次 shape，控制输出声明动态长度，上界和字节算术受检查。
4. 对象绑定 manifest 中 dataset/index 身份与 artifact shape；查询向量对象也归属 dataset 内容身份，queries artifact 另外记录查询选择及顺序。uint64 描述不解除现有 uint32 节点 ID 限制；整数不得经浮点或布尔转换。大小/索引/范围溢出、记录类型不符、错误内容版本、重复对象/op/端口/依赖均拒绝。
5. 七个算子都标记 `schema_supported=true,execution_supported=false,mapping_status=not_implemented`。目录声明端口、允许位置、效果、工作量规则、功能/成本版本、输出及 scratch 上界。只有 SCORE_L2 声明 host/controller 两种目标映射；对前端仍只发布 HostMemory，内部 DeviceBuffer 不作为免费可读输出。控制、邻接及 JOIN 仅 host。
6. 提交的 workload 只选择冻结规则；真实 counts 由后续受信 evaluate 生成，本版拒绝提交者填写 counts。literal 仅允许小标量配置和一个静态入口 ID，不接受距离、向量或完整后继 trace。现有 SCORE/READ 要求完整逻辑字段；合法局部 range 描述不意味着这些算子支持部分读取。
7. 诊断文件静态检查已出现的 producer/端口/类型/shape/查询归属、单调 ID、ADDRESS/DATA 绑定及 access 与地址输入一致。它不创建运行期活动表、值所有权、可见性或等待状态；state_ref 的非零版本和本 query 归属仅为结构检查，版本存活/排他规则仍由 V03-2 实现。非空 state_version 仅允许 ORDER/query_state 组合，不代表已经验证运行期状态发布。

JSON Schema 覆盖结构；跨字段身份、目录 hash、端口与 shape 关系、引用和索引范围等由 C++ 语义检查补齐。独立 JSON Schema 通过不是完整校验通过。校验器仅实现本仓生成 schema 所用关键字，不是通用 JSON Schema 服务；加载整个诊断夹具也不代表未来运行时允许常驻全 DAG。诊断预算执行和流式操作日志属于 V03-5。

## 实际检查与证据

本机主证据为 `results/v0.3/schema-v031/final/manifest.json`，命令/退出码在 `commands.json`，日志、源码清单 SHA256、未提交 diff、导出 schema/catalog 和校验摘要一起保存。该目录被 Git 忽略，新检出按以上命令复建。运行后文档状态更新另存最终 hash，不冒充运行前源码。

| 检查 | 本轮结果 |
| --- | --- |
| V03-0 依赖门槛 | 冻结 manifest hash 与全部产物 hash 重新核对通过 |
| 根 CMake configure / build | 退出0，复用现有锁定 SST 缓存 |
| 完整当前 CTest | 69/69 通过；原67项保留，新增 `ir_schema_catalog` 和 `ir_schema_memory` |
| `tests/test_ir_schema.py` | 9/9 组通过；含两 placement 合法样例、严格拒绝变体、元信息、导出/生成一致性、诊断上下文 |
| C++ 内存 probe | 枚举/variant/端口/shape 实际映射、往返及手工非法内存值拒绝通过；已计入 CTest |
| 独立 `jsonschema 4.26.0` | Draft 2020-12 metaschema 与合法样例通过；仅用于本次独立验证，不新增构建/CTest Python 依赖 |
| `tests/test_direct_baseline.py` | 3/3 通过 |
| Python 语法、`git diff --check`、子模块身份/干净状态 | 通过；MQSim/SST 未修改 |

初轮编译因 JSON 到 set 字符串转换歧义失败；初轮样例还发现了算子顺序生成与 shape 元素读取错误。已修正并重跑；早期失败日志保留在 `results/v0.3/schema-v031/`，不计通过。

本轮不修改 direct 搜索、服务、SST/MQSim 时间或计费，不实现 engine 配置开关；未运行完整 V0.1 AC、原生 oracle、ASan、完整 SIFT/52条矩阵。输入文件的实际可信打开、运行期动态长度/状态版本/可见性/回收以及算子功能/服务等价性仍未证明，分别属于后续任务；hardware 参数继续为 `configured_unvalidated`。本任务结束，不启动 V03-2。
