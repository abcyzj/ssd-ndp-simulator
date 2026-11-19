# MaxSim 合成负载生成与 E0 执行

输入 [实验方案](../../docs/MaxSim_ndp.md) 的参数，生成从查询向量和候选 ID 就绪到 Host Top-K 就绪的语义 DAG。产物可用 V0.4.5 Python SDK 校验，无需构建 SST/MQSim 或准备向量文件。来源为 `synthetic-fixture`，证据覆盖结构、依赖与账本。算子输出仅有字节数和元数据摘要。

## 生成与复检

从仓库根目录执行。使用已安装 [Python SDK 依赖](../../python/README.md) 的 Python 3.9+，例如仓库现有的 `.venv/bin/python`。命令也可在任意工作目录通过绝对路径运行。

```sh
export PYTHONPATH="$PWD/python"
python workloads/maxsim/generate.py describe
python workloads/maxsim/generate.py generate --output /tmp/maxsim-main
python workloads/maxsim/generate.py check --input /tmp/maxsim-main
```

输出路径必须是新目录，其父目录须已存在。生成与校验均成功后才发布输出目录，`receipt.json` 记录命令、工作目录、退出码、仓库和生成器身份，以及输入、产物与 IR 包的 SHA-256。失败保留可用的诊断产物在同级的 `.failed` 或 `.pending` 目录。预检失败会打印原因并返回非零退出码。

## 配置与扫描

[main.json](main.json) 是完整默认配置。通过 `--config <文件>` 传入 JSON 覆盖项。命令依次应用主配置、配置文件、`--point` 扫描覆盖、`--queries` 与 `--seed`。下例切换对照模型、减少候选数并开启开放到达。

```json
{
  "workload": {"model_preset": "colqwen2.5", "candidate_count": 50},
  "cache": {"host_capacity_bytes": 0},
  "load": {"mode": "open_loop", "open_loop_qps": 30},
  "generation": {"queries": 4, "arrival_seed": 23}
}
```

`model_preset` 支持 `colqwen3`、`colqwen2.5`、`colpali` 和 `custom`。ColQwen3 可扫描基准文档长度，自定义维度用 `custom`。模板 token 通过 `template_token_increment` 追加。文档 dtype 自动联动每分量字节数，查询分量字节独立配置。模型引用和可用 revision 保存为配置元数据。

```sh
python workloads/maxsim/generate.py scan
python workloads/maxsim/generate.py scan --batch E1
python workloads/maxsim/generate.py describe --config workloads/maxsim/e0.json --point E0-single-01
python workloads/maxsim/generate.py generate --config workloads/maxsim/e0.json --point E0-single-01 --output /tmp/maxsim-e0
python workloads/maxsim/generate.py check --input /tmp/maxsim-e0
```

[e0.json](e0.json) 固化本机 E0 的 1000 文档页、单查询、缓存关闭和种子组 11。它绑定 [e0-device.xml](e0-device.xml) 的 SHA-256 和 MQSim commit，并导出匹配 16 KiB NAND 页的标准 Layout。配置中沿用生成器字段名 `external_effective_GBps`，本点的 7 GB/s 指 XML 的 PCIe 配置速率，即 4 lane × 1.75 GB/s/lane。E0.1 的设备探针证据见 [实施计划](IMPLEMENTATION_PLAN.md#e01-冻结本机运行配置)。

## 在 SST 中执行 E0

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
export PYTHONPATH="$PWD/python"
python workloads/maxsim/generate.py generate --config workloads/maxsim/e0.json \
  --point E0-single-01 --output /tmp/maxsim-e0
python workloads/maxsim/run.py --input /tmp/maxsim-e0 --build build/delivery \
  --output /tmp/maxsim-e0-run
ctest --test-dir build/delivery -R '^maxsim_e0_execution$' --output-on-failure
```

`run.py` 复检生成凭据、包结构、独立账本、Layout、XML 及 MQSim 身份，然后让三个独立的 SST 进程从同一预装载策略开始执行同一 IR。可重复指定 `--placement` 选择路径。再次执行时使用新的输出目录。每个放置输出 SST 运行配置、可重放命令、退出码、标准输出和标准错误、JSONL 事件以及汇总与哈希。重放单个 SST 配置前，为 `output` 和 `scratch` 指定新的路径。

- `host` 和 `host_compute_free` 提交普通 NVMe 读，CQ 和数据 DMA 到达后释放读配额。两者使用相同的输入门控。免费路径的实际评分批次仍走异步资源，服务时间为 0 ns。
- `device_all` 在命令开销和下行消息完成后提交 MQSim 本地读。读窗口和 QD 在读完成后回收，盘控暂存端口完成搬运后文档才可评分。回传合并为一条全候选分数消息，随后执行相同的 Host Top-K。
- [e0-run.json](e0-run.json) 固化 16 B/ns 盘控暂存、Host DRAM 与 Top-K 零服务，以及下行命令和上行结果各 32 B 的应用协议头假设。每批按最早就绪顺序取最多 8 份文档，同一时刻按候选序号排序。批次成本使用等效 FLOP 和一次启动开销。
- 报告分列有效文档字节、含填充的逻辑请求字节、普通 NVMe 和 MQSim 本地请求、PHY 完成的 NAND 事务、盘控搬运和实际 PCIe 链路服务。NAND 读取放大为总 NAND 读字节（含 FTL 等额外读）除以有效文档字节。PCIe 服务量按方向累加已完成传输的服务时间。Host DRAM 的物理服务和评分内核中归约的独立成本不可观测。

执行入口支持单查询、闭环并发 1、关闭缓存、读算重叠、单侧单评分引擎。实验扫描点超出该执行域时可继续生成元数据。E0 硬件模型状态为 `configured_unvalidated`。

### 复跑 E0.3 验收

在完成上述构建后，从仓库根目录运行下列命令。指定新证据目录可保留主点、三类反压夹具、多文档批和预检失败样本，以及各次运行的有效配置、身份和事件。

```sh
mkdir -p results/maxsim
evidence=$(mktemp -d "$PWD/results/maxsim/e0.3.XXXXXX")
NDPSIM_MAXSIM_EVIDENCE="$evidence" ctest --test-dir build/delivery --output-on-failure
PYTHONPATH=python python workloads/maxsim/test_maxsim.py
```

执行验收逐条重建逻辑读与 NAND 用户页账本，按 NAND 原始区间重算介质和总线占用，并从 PCIe 事件核对分方向排队、服务、到达与协议账本。三个反压夹具各运行全部放置，检查请求在对应配额恢复后继续提交。主点三条路径各复跑一次并比较轨迹字节。

## 扫描与随机种子

[scan.json](scan.json) 声明 E0～E5 的轴。E1 展开容量、分布和封闭并发的指定组合。E3～E5 为单因素扫描。E2 的开放压力点可生成到达序列，选择哪些 E1 点作性能对照须待执行结果。扫描输出包括点 ID、覆盖项、种子组和派生账本。默认生成 1 个查询，实验建议的 10,000 查询记录在 `measured_queries_initial`。预检报告候选、切片、stage、IR/SQLite 估计和输出磁盘预算。默认预算会拒绝将主配置直接生成成 10,000 查询包。

`layout_seed`、`candidate_seed`、`arrival_seed` 分用三个带域标识的随机流。`--seed` 为三路设置同一组种子，配置文件可分别控制。固定置换将热度排名映射到文档 ID。均匀组直接无放回抽样。Zipf 组使用累计权重抽样并拒绝重复，连续拒绝 64 次后用剩余排名的指数竞赛继续按相同分布抽样。开放模式输出累计指数间隔上取整到 ns 的到达时间，封闭模式保留完成后补发规则及并发度。用相同的 Python 构建、生成器源码、配置、种子复现包身份。

## 产物

| 文件 | 用途 |
| --- | --- |
| `effective-config.json` | 展开配置、参数来源、单位字段、派生账本和资源估算。E0 执行复用其中的调度、成本、协议与硬件参数。 |
| `candidates.jsonl` | 每查询的去重文档 ID，按抽样顺序。 |
| `logical-layout.json` | 偏移为 `doc_id × stride`，步长为文档有效大小按 4 KiB 向上取整。含语料逻辑容量下限和排名映射身份。 |
| `layout.json` | 同时显式给出 `layout.namespace_capacity_sectors` 和 `layout.namespace_page_bytes` 时导出 `ndpsim-layout/1`。配置中的 MQSim XML 可按相对路径引用并核验 SHA-256。 |
| `logical-reads.jsonl` | 每个候选的顺序、有效切片和按 4 KiB 补齐的全 miss 请求长度。NAND 页分解及链路流量由执行侧计量。 |
| `arrivals.jsonl` | 仅开放模式生成。每查询一行，单位为 ns。 |
| `scan-definition.json`、`operators.json` | 扫描定义及版本化算子注册表。可变输出规则绑定实现哈希，通过本生成器的 `check` 入口复检。 |
| `package/` | 标准 IR manifest、operations、index 和 tail。每文档的评分等待读取全部有效切片，Top-K 等待全部候选分数。 |
| `structure.json`、`accounting.json`、`receipt.json` | SDK 校验、独立账本校验和整次生成的发布凭据。 |

主配置每查询记录 **98,304,000 B** 有效文档输入、**3,145,728,000 FLOP**、**1,500** 个 64 KiB 全 miss 逻辑读，以及 **21,680 B** 下发、**1,800 B** 返回的有效负载。655.36 GB 语料只占一个逻辑对象。三条路径共用语义包。本机 E0 执行使用 1000 个文档页。

## 首批验收命令

```sh
PYTHONPATH=python python workloads/maxsim/test_maxsim.py
```

用例覆盖主配置账本与双输出复现、模型和 E0～E5 展开、FP32 及模板增量、独立随机流、开放到达、全候选覆盖、尾部补齐、显式 Layout、未来执行参数传递、容量和输出预算，以及非法访问/Work/依赖/输出和失败发布。
