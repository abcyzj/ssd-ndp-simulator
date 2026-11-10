# V0.4.5 可视化工具实施计划

日期为 2026-11-10（Asia/Shanghai）。状态为 V01～V05 已完成并通过总验收。本计划是 V0.4.5 发布后的独立扩展。

## 目标与交付

用已有 Capture IR/2 和仿真结果回答三个问题：指定 query 的算子如何依赖，阶段与资源何时运行，同一个阶段在 Host 和 Controller 放置下有何时序差异。

首版提供独立 Python CLI，生成离线交互式 `dag.html`、可导入 Perfetto 的 `timeline.json` 和记录数据来源及覆盖范围的 `manifest.json`。支持仅提供 capture 包、单次运行和同包 B/C 对比三种模式。以原始 query ID 选取数据。

依据 [Capture IR/2](capture-graph-v0.4.5.md)、[CLI 与报告](cli-v0.4.5.md)、[正式 SSD 结果](ssd-accounting-v0.4.5.md)及 [V0.4.5 发布记录](releases/v0.4.5.md)。

## 目录与接口

```text
visualization/
├── README.md
├── pyproject.toml
├── src/ndpsim_viz/
├── web/
└── tests/
```

工具以版本化产物为接口，独立解析 IR/2、运行报告和 trace 所需字段。Python 工具可独立安装，无需仿真 Python 包、SST 或 MQSim。前端构建锁定依赖，构建产物随工具打包，生成的页面可离线打开。根 CMake、仿真 CLI、capture、模拟调度与计费保持现有接口。所有派生文件及 SQLite 索引写入独立输出目录，输入只读。

## 实施步骤

依赖顺序为 **V01 → V02 → V03 与 V04 → V05**。

| 编号 | 工作内容 | 验收出口 |
| --- | --- | --- |
| V01 数据契约与样本 | 选一个教学夹具和一组 P19 同包 B/C 结果。核对包身份、IR 依赖、CSV 和 trace 字段，登记资源事件到 query、算子、阶段的关联覆盖。定义缺失、失败及不支持字段的处理。 | 保存输入路径、hash、原始 query ID、字段映射和资源关联覆盖表。明确哪些轨道可做阶段归因，哪些只展示时间窗口。 |
| V02 读取与索引 | Python 流式解析产物，按稳定键读取依赖，按 `(query, op, stage)` 对齐阶段。使用 SQLite 缓存重复查询所需的索引，按输入身份隔离缓存。对运行报告中的包身份与实际输入做校验。 | 行序变化时结果等价。包身份不一致、重复阶段键和悬空依赖给出明确错误。缺失或未完成的阶段显式标记。 |
| V03 交互式 DAG | 使用 Cytoscape.js 与 ELK layered。默认展示单 query 的折叠算子图，可展开到阶段，搜索节点，高亮前后继，查看 Work、阶段键和可用时序。 | 教学样本的分支与 JOIN 可核对。正式 query 可展开 SCORE_L2 的 prepare、L2、append。折叠后的边可回查对应的阶段边和依赖类型。 |
| V04 时序导出 | 生成 Perfetto 可读的 Chrome Trace JSON。按 query、阶段及可观察的 CPU、Controller、PCIe、SSD 资源分轨。对并发使用异步事件或独立轨道。保留原始 SST 纳秒时间和可用阶段键。 | 在 Perfetto 核对端点、持续时间、并发、同刻与零时长事件。缺关联键的资源事件使用独立轨道。覆盖范围随导出记录。 |
| V05 对比与交付 | 按同包、同一原始 query 对齐 Host 和 Controller，共用 DAG，并在同一份导出中分轨展示两次运行。补齐 CLI 使用说明及复现记录。 | 可从 DAG 复制阶段键并定位 B/C 对应时间片。只凭已有产物，在无 SST、MQSim 和仿真 Python 包的环境中完成转换，并记录正式样本的耗时、峰值 RSS 和输出大小。 |

## 显示与解释规则

- 依赖边来自 IR 显式依赖。阶段身份以稳定键为准，采集记录的物理顺序仅用于读取。
- 阶段区间为 `submitted → completed`，覆盖输入、资源服务和必要返回。有完整时间戳的资源事件分别显示排队 `submitted → start` 和服务 `start → end`。PCIe 使用明确配对的 `enqueue`、`start`、`arrive` 事件。
- 纯依赖 ready 等待标为 `Unsupported`。缺明确关联键的事件可在同一时间窗口观察，阶段归因只使用显式键。
- B/C 分别以本次 query 的 admission 对齐。保留源时间戳、placement、profile 和配置身份。需要查看 admission 前的事件时保留负相对时间，或在导出时记录统一显示偏移。SSD 轨道沿用 `configured_unvalidated` 标记。
- 正式规模按 query 或时间窗口导出。C01 的操作文件约 105 MB，trace 约 186 MB。浏览器只加载生成的子集。

## 验证与证据

小型夹具覆盖显式分支与汇合、算子折叠、同刻和并发区间、缺失关联及包身份不匹配。使用正式 B/C 同包结果，从源文件独立抽查同一 query 的依赖边、阶段提交与完成时间、资源区间及两侧时间零点。记录输入身份、配置、工具版本、运行命令、退出码、断言、产物 hash 和资源用量。

首版通过标准为同一阶段可以从 IR 依赖边追溯到 Perfetto 时间片，并能在 B/C 两组轨道中准确定位。Graphviz 静态导出、DAG 与时序同页双向联动、新增 ready 观测分别留作后续工作。

## 串行执行清单

调度顺序为 V01 → V02 → V03 → V04 → V05。V03 与 V04 均依赖 V02，V05 等两项验收通过后启动。开发范围为 `visualization/`；本节记录主 Agent 独立复核后的状态。

| ID | 名称与目标 | 前置 | 模块 | 验收与测试 | 状态 |
| --- | --- | --- | --- | --- | --- |
| V01 | 数据契约与样本，确定可读字段和归因范围 | 无 | 契约、证据 | 核对教学和正式 B/C 样本身份、原始 ID、依赖、事件字段及覆盖 | DONE |
| V02 | 读取与索引，按稳定键提取查询 | V01 | Python 解析、SQLite | 行序等价、身份不符、重复键、悬空边和未完成阶段 | DONE |
| V03 | 交互式 DAG，按算子折叠及展开阶段 | V02 | 前端、离线页面打包 | 教学分支汇合、正式 SCORE_L2 展开、依赖类型回查、离线交互 | DONE |
| V04 | 时序导出，按运行及资源保留区间 | V02 | Python Trace 导出 | Perfetto 核对并发、同刻、零时长、区间端点及缺键独立轨道 | DONE |
| V05 | 对比与交付，串接 CLI 并核对同包双运行 | V03、V04 | CLI、安装包、使用说明 | 隔离安装与转换、正式 B/C 阶段追溯、耗时/RSS/大小及完整回归 | DONE |

- **V01 DONE**，worker `v01`。新增 `visualization/DATA-CONTRACT.md`、`visualization/evidence/audit_v01.py` 和 `visualization/evidence/v01-samples.json`。主 Agent 重跑 `python3.13 visualization/evidence/audit_v01.py`，输出与登记证据逐字节一致，`git diff --check` 通过。教学 query 0 核对两条 DATA 边，正式 B01/C01 query 2579 核对 607 阶段、1,550 边及 CSV/trace 端点。继承决策为教学使用 capture-only，资源区分完整阶段键、仅 query 和仅窗口，未发出端点标为 Unsupported。

- **V02 DONE**，worker `v02`。新增 `visualization/src/ndpsim_viz/{__init__,reader}.py`、`visualization/tests/test_reader.py`。主 Agent 重跑 `PYTHONPATH=visualization/src python3.13 -m unittest discover -s visualization/tests -v`（8/8），并以新临时缓存复核两包身份及 B/C 各 607 completed、admission 157747495785 ns（退出 0）。下游继承 `Package.query`、`Run.request`、`Run.stage_timings` 和流式 `Run.events`。

- **V03 DONE**，worker `v03`。新增 `visualization/web/`、`src/ndpsim_viz/dag.py` 及 `assets/`、`tests/test_dag.py` 和 `evidence/{render_v03.py,check_v03_browser.mjs,v03-dag.md}`（后四处均在 `visualization/` 下）。主 Agent 重跑 Python 10/10、`node --test visualization/web/graph.test.js` 2/2 和 `node visualization/evidence/check_v03_browser.mjs /tmp/ndpsim-v03-test` 退出 0。Chrome 仅访问两份本地页面，正式展开显示三阶段。继承 `write_dag` 接口和紧凑 JSON 阶段键（query 使用十进制字符串），安装包需包含 `assets/*`。

- **V04 DONE**，worker `v04`。新增 `visualization/src/ndpsim_viz/timeline.py`、`visualization/tests/test_timeline.py`、`visualization/evidence/{check_v04.py,v04-timeline.md,v04-timeline.json}`。主 Agent 重跑 Python 13/13 及 V04 记录中的 Perfetto 59.1 检查（退出 0），导入 29,103 切片，1,214 个正式阶段逐纳秒对应源 CSV，夹具 14 切片验证并发、同刻和零时长。继承 `write_timeline` 及返回的覆盖记录，Perfetto SQL 用 `args.stage_key` 精确定位。

- **V05 DONE**，worker `v05`。新增 `visualization/{README.md,pyproject.toml,src/ndpsim_viz/cli.py,tests/test_cli.py,evidence/{check_v05.py,v05-delivery.md,v05-delivery.json}}`。主 Agent 在新虚拟环境安装独立 wheel，运行 `visualization/evidence/check_v05.py`（退出 0），三模式均通过。正式转换复跑 25.826 秒，峰值 RSS 69,189,632 B，三个产物共 12,767,757 B，另有 56,250,368 B SQLite。Perfetto 以 DAG 键 `op2/score_l2.1` 定位两侧并与源 CSV 核对。继承 CLI 新输出目录和 manifest 完成标记。Python 16/16、Node 2/2，根项目回归另见总验收。

## 总验收

V01～V05 由五个专职 worker 串行完成，主 Agent 逐项复核后放行。根项目执行 `cmake -S . -B build/delivery -DBUILD_TESTING=ON`、`cmake --build build/delivery -j2`、`ctest --test-dir build/delivery --output-on-failure`，均退出 0，CTest 82/82（95.37 秒）。构建复用锁定 SST 缓存，MQSim 头文件有编译警告。可视化 Python 16/16、Node 2/2、Chrome 离线及 Perfetto 导入均通过。

模块兼容性由隔离 wheel 三模式、正式同包 B/C 和阶段键交叉核对覆盖。验证使用既有正式运行产物，资源可观测范围沿用数据契约。纯依赖 ready 等待和无端点的资源服务保持 Unsupported，SSD、PCIe 和 Controller 保持 configured_unvalidated。既有 quickstart 修改保留，未提交或推送。
