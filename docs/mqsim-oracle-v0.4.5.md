# P16 独立 MQSim 对拍工具

日期：2026-11-06（Asia/Shanghai）。依据 [PRD FR11／AC05](prd-v0.4.5.md)、[源码核查 SA-15](source-audit-v0.4.5.md)及[实施计划 P16](implementation-plan-v0.4.5.md)。P16 已完成，P17～P21 未开始；本任务提供 AC05 共同设备行为证据，不宣布最终版本或12项AC总验收。当前可运行交付仍为 V0.4。

[独立入口](../tools/verify_mqsim.py)不导入或调用 `verify_acceptance.py`、旧总AC、旧图搜索运行器。输入为当前已有集成构建、`deps.lock.json`、外部XML样例和工具内冻结的请求矩阵；不需要历史 results 或业务 payload。先显式构建，再运行：

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_mqsim.py \
  --build build/delivery --output results/v0.4.5/p16/new-oracle-run
```

Python／编译器使用本机锁文件中的路径；示例Python路径对应已验证的macOS arm64环境。工具只构建新的独立原生oracle，不自动配置或构建集成后端，不跑旧AC，也没有quick通过模式。`--output` 必须不存在；`--timeout`为每命令1～3600秒，默认600秒。失败保留命令、stdout/stderr、错误和 `status=failed` manifest，非零退出；重复输出目录拒绝且不覆盖证据。通过也始终 `full_acceptance_passed=false`。

原生从锁定 commit `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16` 执行 `git archive` 独立导出，直接编译导出树全部非main C++源码；包含路径优先使用导出树，未链接fork库。保存archive、逐文件源码hash、编译命令及编译器身份、二进制hash，并复核导出源码未修改。集成侧运行真实 `ndpsim.OrdinaryIO` SST组件，经EventBridge推进后端；原生手动循环只用于oracle探针，不成为生产时钟。工具保存集成MQSim完整commit、相对原生diff和实际逐文件hash，检查运行期间源码、输入和构建身份未变。

## 固定请求、初态与独立核对

每组写出 `experiment.json`、实际 `device.xml` 和 `requests.txt`。请求文件第一行为 `ndpsim-mqsim-requests/1`，后续每行为 `read LBA sectors`；仅支持1～32项、最多4096字节、预装载范围内非零只读访问。解析在设备构造前完成；未知格式、越界、数量失配、额外列均拒绝。两个程序读取同一个绝对请求路径，XML相对声明文件解析。驱动仍保留旧夹具入口供迁移期回归，本任务不删除旧路径。

| 配置 | 共同输入与重复 |
| --- | --- |
| QD1 | 1个LBA7／10sector读；原生2次、真实SST2次 |
| QD32 | 32项，LBA=`7+(i%16)*16`、每项10sector；两边各2次 |
| small-CMT | 同QD32，外部XML的CMT改为128B，必须出现read CMT miss；两边各2次 |
| modified-XML | 同QD32，XML中PCIe bandwidth=2、lanes=2，三类NAND read=90000ns，seed=123、queue=32、fetch=2；两边各2次 |

QD表示预装载排空后一次提供的请求数，不冒称硬件SQ深度；默认XML队列深度仍为16。四组均先从LBA0开始串行普通整页写满512sector，保留FTL/CMT初态及后台事件，完全排空后冻结origin，再同时提供测量读。最终CQ／doorbell与所有后端事件排空后记录结束时间，不重置后端时钟或累计统计。

共同fixture、ExternalFlow与PreloadWindow属于共享输入驱动的信任边界；它们相等不足以自证正确。因此工具另外用Python独立解释XML和请求，核对实际应用的17个参数、实际设备namespace/flow末LBA、预装载页及请求提交顺序/LBA/size/type、token唯一性与完整完成、测量窗口、每项跨页事务数、初态page program数、全部64页的映射覆盖/PPA唯一性、CMT容量/状态/保留条目、统计关系和排空。独立账本不调用被测C++配置/映射实现。

原生通过上游自身 `XML_deserialize` 应用同一XML；集成侧通过P10 typed activation，不共用参数赋值。XML的hash、实际生效参数和独立解释结果须一致；修改用例还要求实际模拟时间改变。所有共同输出行按原顺序逐字比较：APPLIED、CONFIG、NAMESPACE、SUBMIT、IO、INITIAL、CMT、CACHE、MAP、STATS initial/final、DRAIN、FINAL；完成时间差1ns或同刻完成次序不同均失败，不设百分比容差。原生及SST的两次重复也必须确定性一致。

16个统计字段覆盖普通NAND read/program/erase命令、mapping read/write、全局及read/write CMT hit/miss/query、GC/WL执行。上游全局 `CMT_miss` 仅统计立即解析的miss，typed miss还包含延后解析；按源码分别核验守恒及不等式，原始计数始终严格对拍。small-CMT时上游部分已驱逐页GMT的written bitmap可为0，不能将全部GMT字段断言为满页；本工具核对其合法范围、驻留CMT页满bitmap，并对全部MAP原始值严格比较。普通写请求账本只证明请求/时序初态，不声称验证业务内容。

## 实际检查与证据

直接依赖P10 run1、P12 run2的manifest和322项登记产物hash重新核对通过。最终证据为 `results/v0.4.5/p16/run1/` 及 `oracle3/`；包含命令/cwd/退出码、原始输出、有效XML/请求、独立账本、源码/锁/构建/二进制/产物hash。

- 根配置及构建退出0；最终完整根CTest **139/139** 通过。
- `tools/verify_mqsim.py --build build/delivery --output results/v0.4.5/p16/oracle3`：**4组×原生2次+SST2次，共16次**全部严格一致。small-CMT测量期read miss=66；QD32初态origin=53811904ns，modified-XML为54771776ns，修改生效。配置切换期间未重编译平台。
- `tests/test_v045_mqsim.py build/delivery`：**7/7**。每次新生成四组真实SST输出，从外部cwd、Unicode和空格路径读取配置/请求；覆盖缺失/重复记录、相同错误同时污染两边、LBA/size/顺序/配置/初态/统计/时间篡改、small-CMT必需miss、非法请求预分配拒绝、失败manifest及输出不可覆盖。CTest运行此套断言，独立原生导出矩阵由上面的专用命令另外执行。
- ASan重新配置/构建ordinary探针，既有普通读CTest **2/2**；新增四组显式请求路径运行及Python独立账本 **4/4**，未增加泄漏豁免。未宣称SST全栈或上游完整泄漏审计。
- P01设计 **11/11**、P00门控 **3/3**，逐文件迁移审计 **404文件／139CTest**，文档链接、证据hash及 `git diff --check` 通过。

保留开发失败：首轮独立统计断言错误地假设全局hit+miss等于total，修正为上游的typed/global语义；随后修正对small-CMT已驱逐GMT bitmap的假设，未修改上游或放宽共同字段精确比较。`dev1/`、`oracle1/`和定向失败日志仍保留；`oracle2/`为增补实际namespace断言前的通过，最终出口使用 `oracle3/`。根CTest首轮137/139，两项CLI/Validator测试因沙箱禁止只读 `sysctl hw.memsize` 失败；允许该预检后以最终构建重跑全部139项，未跳过或改写资源门槛。

## 限制

对拍限定当前共同的单NVMe、只读测量／整页写初态、关闭data-cache、512sector预装载和上述四组参数。不是任意用户XML、长期写负载、GC/WL压力、完整后端状态或真实SSD校准；其余高级命令计数不纳入本冻结矩阵。非共同stdout诊断、SST启动/结束文字、宿主墙钟/RSS不作为设备等价字段；排除理由写入manifest。

上游没有fork的数据到达回调，原生不核验fork特有DMA内容门控；它不具备local-read、Controller计算或平台消息能力，这些继续由P12/P19验证。功能payload、Controller卸载及真实硬件准确性均不由本oracle证明，SSD/PCIe/Controller保持 `configured_unvalidated`。正式SIFT规模、CPU精度、P19账本及P21总验收未执行；P17未开始。MQSim/SST与依赖锁均未修改，没有提交、推送或初始化Git。
