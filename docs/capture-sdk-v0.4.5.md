# P02 通用校验与采集 SDK

日期：2026-11-06（Asia/Shanghai）。状态：**P02完成，P03～P21未开始，全部AC仍待验收**。依据 [PRD FR03/04/09](prd-v0.4.5.md)、[实施计划P02](implementation-plan-v0.4.5.md)与[P01冻结契约](contract-v0.4.5.md)。P01的三个证据manifest及所列全部产物已重新核验hash/通过状态，结果保存在本次 `p01-dependency.json`；未以旧通过代替P02检查。

## 实现与边界

| 交付 | 实际能力 |
| --- | --- |
| [独立C++17 SDK](../sdk/README.md)、[公共头文件](../sdk/include/ndpsim/capture.h) | 显式声明OperatorSpec/Object/Operation及多stage，提交Work/特征和DATA/ADDRESS/CONTROL/ORDER边；线程互斥写规范JSONL，增量SHA256，预算/失败中止，完整结束marker绑定原始流大小/hash。提供Measurement样本/clock/sampling元数据类型与样本编码，不自动读取业务或时钟。 |
| [通用封包与校验](../python/ndpsim/static_ir.py) | Python增量writer、C++采集流封包、manifest/index/tail完整性、外部registry/可选外部包和provenance身份绑定、单记录schema及跨记录关系校验。 |
| [可安装Python包](../python/README.md) | 不依赖旧tools、SST/MQSim、编译器或业务payload；开发入口为`python -m ndpsim.capture_cli seal/check`，不是P14统一用户CLI。 |
| [外部注册清单](../examples/p02-capture/registry.json)、[完整合成包](../examples/p02-capture/package/manifest.json) | 一个请求、4操作/4阶段/4依赖，物理记录故意重排；新包明确标synthetic-fixture。它只验证结构/依赖表达，没有冒称真实块求和或图搜索采集。 |
| [回归测试](../tests/test_v045_capture.py) | SDK/安装边界、独立hash/字节位置核对、完整性及关系负例、预算/永久失败/发布保护、外部cwd及空格路径。 |

C++与Python均以P01的数据契约为准；未改动冻结schema。Python安装包内携带两份必要schema，测试要求与根schema字节完全一致。C++用本地JSON编码和项目MIT SHA256的流式改编，不借用SST external或引入下载依赖；SDK/Python保留MIT许可证。两套独立hash算法（C++与Python/hashlib）核对空串、3/55/56/63/64/65及百万字节、增量分块和不修改状态的digest；跨语言编码另核对Unicode、代理对和DEL控制字符。

P01的抽象writer职责实现时分成两步：C++ `Recorder.close(terminals)`完成**未封装采集流**，Python `seal_capture`负责通用schema/跨记录验证和原子发布。这样原生链接不需要Python或JSON第三方库，同时不能把“写完JSON”当成包已验证。没有修改旧Recorder/Replay路径；新的公共层不包含图搜索操作白名单、固定循环或业务功能计算。旧路径留待P20按替代证据删除。

## 已实现的门控

校验器检查原始query ID/顺序及完整覆盖、对象版本/访问范围/空访问、uint64与溢出、注册算子/语义版本/阶段/上下文/分解规则、Work和特征单位/字段、纯元数据输出大小规则、端口类型/单位/required/variadic基数、四类边及缺源/环/重复、连续逻辑rank、不可变查询内状态版本、全部sink与显式terminal一致。

独立扫描操作流生成期望索引，再逐项比对索引的byte offset/length/stage摘要/依赖和覆盖；同一operation多stage共享行范围合法。manifest/tail计数与实际流重新核对，检查完整文件SHA256及包ID，拒绝截断、篡改、重复JSON键、非有限数、整数浮点写法、非法Unicode、路径逃逸、符号链接和额外文件。物理行可重排，通用层不凭读取次序补ORDER依赖。

所有新输出必须不存在。writer异常永久失效；捕获异常后仍不能继续或seal。先在pending写完整并独立预检，再发布；失败保留failed/pending目录，没有有效输出。锁序列化遵循同协议的发布者；不宣称崩溃持久化fsync或对恶意并发替换目录的防御。校验/使用期间输入仍须保持不可变，工具重查hash和文件状态，hash不认证生产者。

`OperatorRegistry`来自调用方独立提供的契约，其指纹包括单位/OperatorSpec及size-rule描述；不能从待验证包推导可信规格。CLI只支持声明式固定输出大小规则；在进程中可显式注册有实现hash的纯元数据callback，不加载任意脚本或动态插件。图专用大小/语义/状态准备归属规则由P04扩展实现，不在通用层重写。缺少required边可结构拒绝；业务上本应存在、但未在通用端口契约表达的边，仍须业务语义验证，不能凭结构报告推断完整业务正确性。

结构报告始终分列：`structural_passed=true`、`semantic_validation="not-performed"`、`independent_semantic_passed=false`。native-capture来源和内容hash也不能升级这个等级。生产入口后续须绑定独立语义报告；P02贡献AC02的SDK/通用包部分，不关闭还依赖P04/P08/P15的完整AC。

## 最小复现

```sh
cmake -S sdk -B build/capture-sdk -DNDPSIM_CAPTURE_TESTING=ON
cmake --build build/capture-sdk -j2
ctest --test-dir build/capture-sdk --output-on-failure
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tests/test_v045_capture.py \
  build/capture-sdk/capture_fixture

# 无需构建，直接检查仓库中的完整合成包；report必须为新文件
PYTHONPATH=python python3 -m ndpsim.capture_cli check \
  --registry examples/p02-capture/registry.json \
  --input examples/p02-capture/package --allow-fixture \
  --report <new-report.json>
```

源码接入、`find_package(ndpsimCapture)`安装接入、C++关闭流后seal命令、Python API和依赖范围见 [SDK说明](../sdk/README.md)及[Python说明](../python/README.md)。阶段/Work/依赖必须由实际调用方提供，没有自动插桩或业务依赖推断。

## 实际检查、身份与限制

本次证据在 `results/v0.4.5/p02/run1/` 与最终 `run2/`，含reproduce、commands/退出码、原始log、源码前后snapshot、完整依赖/相对原生diff、有效registry和输入/构建/产物hash。

实际执行并通过：

- 只复制`sdk/`到隔离目录，独立CMake配置/编译/CTest **2/2**；安装后外部consumer使用`find_package`链接运行通过。`otool -L`只有libc++/libSystem，CMake缓存无SST/MQSim发现。
- Python项目复制到隔离目录，以`pip install --no-index --no-deps --no-build-isolation --target ...`构建安装；使用当前已安装的jsonschema/referencing，不访问网络或更改系统安装。安装后的模块在外部cwd、带空格输入/输出路径完成seal/check，未引用源码包或历史results。
- P02 **16/16测试**通过，含25组关系负例、13组文件/索引/尾记录负例及其他注册/数值/预算/生命周期检查；使用隔离构建的实际SDK二进制。P01设计测试 **11/11**、P00门控 **3/3**通过。
- `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure`通过，根CTest **93/93**，27.66秒，只作为当前V0.4回归。
- 迁移审计 **301文件/93根CTest**，新增SDK/Python纳入逐文件清单和引用检查；独立SDK测试另列，不伪装成根CTest新增。`git diff --check`通过。

最终Unicode编码边界修订后，`run2/`重新隔离构建/安装SDK、运行16项测试和2项SDK CTest、外部consumer及安装后seal/check，并核对根生产src/CMake/锁、二进制/flags/SST receipt与run1完全相同，复用同身份的93项根回归，没有重复执行未变生产实现的完整检查。运行期间源码及依赖不变；收口文档身份另存closeout。

MQSim/SST及根模拟CMake/生产src/锁均未修改；MQSim fork、锁定原生commit和相对diff仍沿P00/P01身份，完整值见本次`dependencies.json`。没有提交、推送、升级或初始化Git。

校验关系状态使用磁盘SQLite索引，2MiB缓存，逐条有界JSONL扫描，不在Python保存全查询操作列表。默认记录/查询索引上限16MiB、每查询stage上限100000、包上限2GiB、SQLite数据库页预算4GiB；包声明不能提高部署限制。测试覆盖41查询（含最大uint64 ID）及164阶段，Python分配峰值低于32MiB，最大单查询活跃索引4阶段。它不是全规模RAM/磁盘可行性证明，也不是硬RSS/全部临时文件系统隔离；正式操作前仍须重新取资源和pilot预算。

本次只产生synthetic-fixture和结构证据。未实施独立图搜索拆分、完整真实IR采集/独立Work核对、运行期调度、CPU测量拟合/精度、SSD正式映射或完整验收。P00记录的原锁定NSG/prepared缺口仍需在正式任务前解决。P03及后续任务未开始，全部新AC保持待验收。
