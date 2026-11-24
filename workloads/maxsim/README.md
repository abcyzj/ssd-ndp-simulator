# MaxSim 合成负载与 E0/E1/E2 执行

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

[main.json](main.json) 是完整默认配置，语料为一万页。通过 `--config <文件>` 传入 JSON 覆盖项。命令依次应用主配置、配置文件、`--point` 扫描覆盖、`--queries` 与 `--seed`。下例切换对照模型、减少候选数并开启开放到达。

```json
{
  "workload": {"model_preset": "colqwen2.5", "candidate_count": 50},
  "cache": {"host_capacity_bytes": 0},
  "load": {"mode": "open_loop", "open_loop_qps": 30},
  "generation": {"queries": 4, "arrival_seed": 23}
}
```

`model_preset` 支持 `colqwen3`、`colqwen2.5`、`colpali` 和 `custom`。ColQwen3 可扫描基准文档长度，自定义维度用 `custom`。模板 token 通过 `template_token_increment` 追加。文档 dtype 自动联动每分量字节数，查询分量字节独立配置。模型引用和可用 revision 保存为配置元数据。

`host_capacity_fraction` 按语料的有效向量字节数换算缓存容量，向下取整。默认比例为 5.24288%，在一万页 FP16 主配置下为 343,597,383 B。E1 扫描 0%、1.31072%、5.24288%、20.97152%，可用 `scan` 输出的 `host_capacity_bytes` 查看各点容量。单独指定 `host_capacity_bytes` 可切换到固定容量，例如本机 E0 使用 0。

```sh
python workloads/maxsim/generate.py scan
python workloads/maxsim/generate.py scan --batch E1
python workloads/maxsim/generate.py describe --config workloads/maxsim/local-10k.json --point E0-single-01
```

[local-10k.json](local-10k.json) 为本机 E0 提供一万页、单查询和零缓存配置，并绑定 [8 GiB raw 设备 XML](local-10k-device.xml) 的 SHA-256 和 MQSim commit。设备使用 16 KiB NAND 页、256 页/块和 7% OP，逻辑 namespace 为 15,602,784 个 512 B sector。一万页 FP16 Layout 占前 12,800,000 个 sector，需预装载 400,000 个 NAND 页。后续重跑使用同一 XML 中的 28 GB/s PCIe 配置速率（4 lane × 7 GB/s/lane）和 16 条 1 B 宽、3600 MT/s 的 NAND 通道。NAND 总线理论带宽为 57.6 GB/s。

## 在 SST 中执行 E0

3600 MT/s 夹具使用完整 NAND 事务字节数计算传输耗时，再向上取整到 ns。复跑时使用实际构建对应的 MQSim 源码。若高速计时修改尚在本地工作树中，向运行入口传入显式的 `--backend-source-sha256`。

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
export PYTHONPATH="$PWD/python:$PWD/workloads/maxsim"
backend=$(python -c 'from run import backend_digest; print(backend_digest())')
python workloads/maxsim/generate.py generate --config workloads/maxsim/local-10k.json \
  --point E0-single-01 --output /tmp/maxsim-local-10k
python workloads/maxsim/run.py --input /tmp/maxsim-local-10k --build build/delivery \
  --output /tmp/maxsim-local-10k-run --backend-source-sha256 "$backend"
NDPSIM_MAXSIM_BACKEND_SOURCE_SHA256="$backend" \
  ctest --test-dir build/delivery -R '^maxsim_e0_execution$' --output-on-failure
```

`run.py` 复检生成凭据、包结构、独立账本、Layout、XML 及 MQSim 身份，然后让三个独立的 SST 进程从同一预装载策略开始执行同一 IR。可重复指定 `--placement` 选择路径。再次执行时使用新的输出目录。每个放置输出 SST 运行配置、可重放命令、退出码、标准输出和标准错误、JSONL 事件以及汇总与哈希。重放单个 SST 配置前，为 `output` 和 `scratch` 指定新的路径。

- `host` 和 `host_compute_free` 提交普通 NVMe 读，CQ 和数据 DMA 到达后释放读配额。两者使用相同的输入门控。免费路径的实际评分批次仍走异步资源，服务时间为 0 ns。
- `device_all` 在命令开销和下行消息完成后提交 MQSim 本地读。读窗口和 QD 在读完成后回收，盘控暂存端口完成搬运后文档才可评分。回传合并为一条全候选分数消息，随后执行相同的 Host Top-K。
- [e0-run.json](e0-run.json) 固化 64 B/ns 盘控暂存、Host DRAM 与 Top-K 零服务，以及下行命令和上行结果各 32 B 的应用协议头假设。每批按最早就绪顺序取最多 8 份文档，同一时刻按候选序号排序。批次成本使用等效 FLOP 和一次启动开销。
- 报告分列有效文档字节、含填充的逻辑请求字节、普通 NVMe 和 MQSim 本地请求、PHY 完成的 NAND 事务、盘控搬运和实际 PCIe 链路服务。NAND 读取放大为总 NAND 读字节（含 FTL 等额外读）除以有效文档字节。PCIe 服务量按方向累加已完成传输的服务时间。Host DRAM 的物理服务和评分内核中归约的独立成本不可观测。

E0 入口支持无缓存的单查询执行。E0 和 E1 均使用读算重叠、单侧单评分引擎，硬件模型状态为 `configured_unvalidated`。

### 复跑 E0 执行验收

在完成上述构建后，从仓库根目录运行下列命令。指定新证据目录可保留主点、三类反压夹具、多文档批和预检失败样本，以及各次运行的有效配置、身份和事件。

```sh
mkdir -p results/maxsim
evidence=$(mktemp -d "$PWD/results/maxsim/e0.3.XXXXXX")
NDPSIM_MAXSIM_EVIDENCE="$evidence" ctest --test-dir build/delivery --output-on-failure
PYTHONPATH=python python workloads/maxsim/test_maxsim.py
```

执行验收逐条重建逻辑读与 NAND 用户页账本，按 NAND 原始区间重算介质和总线占用，并从 PCIe 事件核对分方向排队、服务、到达与协议账本。三个反压夹具各运行全部放置，检查请求在对应配额恢复后继续提交。[千页 E0 夹具](e0.json)的三条路径各复跑一次并比较轨迹字节，一万页本机配置也执行三条路径并核对事件账本。

## E0.4 百万文档

[e0.4.json](e0.4.json) 保留 E0 的单查询、FP16 形状和地址语义。配套的 [设备 XML](e0.4-device.xml) 使用 1 TiB raw、16 KiB 页、256 页/块和 7% OP。逻辑 namespace 为 1,997,159,776 个 512 B sector，百万文档 Layout 覆盖前 1,280,000,000 个 sector，共 655,360,000,000 B。生成只写元数据。

```sh
export PYTHONPATH="$PWD/python"
python workloads/maxsim/generate.py generate --config workloads/maxsim/e0.4.json \
  --point E0-single-01 --output /tmp/maxsim-e04
python workloads/maxsim/generate.py check --input /tmp/maxsim-e04
python workloads/maxsim/run.py --input /tmp/maxsim-e04 --build build/delivery \
  --output /tmp/maxsim-e04-preflight --preflight-only \
  --device-limits workloads/maxsim/e0.4-device-limits.json
```

`--preflight-only` 在 SST 中使用实际执行入口核验设备、Layout 和读计划，输出 `device-preflight-report.json` 后结束。它在分配 MQSim 前运行。全量预装载需 40,000,000 页，设备元数据估算为 18,257,870,848 B。[DeviceLimits](e0.4-device-limits.json) 显式给出 18 GiB 元数据上限和 4,000 万页上限。

实际执行还需要主机预算。以下为单查询、逐放置执行的预算示例。运行时会先比较设备估算与 RSS 预算，再检查本机物理和可用内存、输出与 SQLite 的磁盘空间。每条路径分别记录超时、退出码、墙钟和子进程峰值 RSS。RSS 在子进程退出时核对。

```sh
cat > /tmp/maxsim-e04-budget.json <<'EOF'
{
  "rss_bytes": 25769803776,
  "output_bytes": 134217728,
  "scratch_bytes": 134217728,
  "min_disk_free_bytes": 1073741824,
  "timeout_seconds": 7200
}
EOF
python workloads/maxsim/run.py --input /tmp/maxsim-e04 --build build/delivery \
  --output /tmp/maxsim-e04-run --device-limits workloads/maxsim/e0.4-device-limits.json \
  --budget /tmp/maxsim-e04-budget.json
```

每个 SST 进程逐页预装载整个 Layout，排空后才进入测量窗口。全量墙钟需在目标机实测。增加查询量须另行核验 IR、索引、trace 和输出预算，当前百万页 E0.4 执行域为单查询。百万 FP32 和千万 FP16 超出当前 MQSim 几何上限。

## E1 闭环缓存实验

`e1.py` 在本机一万页设备上运行 E1 扫描。每个分布和种子共用候选 IR。主机两条路径使用同一个预热快照，分别按实际读完成和命中事件更新 LRU。盘侧全量下沉复用同一分布、种子和并发下的零缓存结果。每个查询完成后立即补发，读取以文档为单位在查询间轮转，评分批仅在同一查询内组合。

```sh
export PYTHONPATH="$PWD/python:$PWD/workloads/maxsim"
backend=$(python -c 'from run import backend_digest; print(backend_digest())')
python workloads/maxsim/e1.py --build build/delivery --output results/maxsim/e1-run \
  --backend-source-sha256 "$backend" --queries 144 --seed 11
```

用重复的 `--point` 和 `--seed` 选择扫描点和种子。默认 10,000 查询一窗的 LRU 重放只用于预热。用 `--cold` 单独跑空缓存起点。测量 QPS 按整段有限闭环完成量计算，避免短窗口恰好落在高并发完成批次之间。短运行的 P99 只作描述。原始轨迹保留每个查询的开始、可见和排空时间，以及 NAND 利用率和资源汇总。完整逐事件对拍使用 E0 入口。夹具的 `Queue_Fetch_Size=1` 会限制普通 NVMe 路径并行度，需将该配置作为结果的适用条件。

## E2 开放到达实验

`e2.py` 复检 E1 的一万页候选包，并复用同组预热快照。每组放置共享同一独立随机流生成的泊松到达时刻。测量从预装载排空开始，查询按到达顺序准入，同时最多执行 16 个。入口等待从到达计到准入，精排延迟从到达计到 Top-K 可见。

```sh
export PYTHONPATH="$PWD/python:$PWD/workloads/maxsim"
python workloads/maxsim/e2.py --build build/delivery \
  --e1 results/maxsim/e1.Y8emQw --output results/maxsim/e2-open
```

种子 11 覆盖 10/30/100/300/1000 QPS 及临界加密点，种子 23 和 47 复测关键点。可重复使用 `--scenario`、`--rate` 和 `--seed` 选择子集。每点使用已核验的 144 次查询。在最后一次到达时截取完成数、入口排队和在途数，随后排空这批请求以复检资源与流量账本。窗口内延迟仅对后半段到达且在截点前完成的查询统计。判断持续积压时结合后半段积压增长、入口等待和种子间变化。过载点的窗口内延迟不代表稳态尾延迟。

## 扫描与随机种子

[scan.json](scan.json) 声明 E0～E5 的轴。E1 展开容量、分布和封闭并发的指定组合。E2 从 E1 选择零缓存均匀、5.24288% Zipf 0.8 和 20.97152% Zipf 1.2，分别覆盖明显收益、默认局部性和下沉收益反转附近。E3～E5 为单因素扫描。扫描输出包括点 ID、覆盖项、种子组和派生账本。默认生成 1 个查询，实验建议的 10,000 查询记录在 `measured_queries_initial`。预检报告候选、切片、stage、IR/SQLite 估计和输出磁盘预算。默认预算会拒绝将主配置直接生成成 10,000 查询包。

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

主配置每查询记录 **98,304,000 B** 有效文档输入、**3,145,728,000 FLOP**、**1,500** 个 64 KiB 全 miss 逻辑读，以及 **21,680 B** 下发、**1,800 B** 返回的有效负载。6.5536 GB 语料只占一个逻辑对象。三条路径共用语义包。

## 首批验收命令

```sh
PYTHONPATH=python python workloads/maxsim/test_maxsim.py
```

用例覆盖主配置账本与双输出复现、模型和 E0～E5 展开、FP32 及模板增量、独立随机流、开放到达、全候选覆盖、尾部补齐、显式 Layout、未来执行参数传递、容量和输出预算，以及非法访问/Work/依赖/输出和失败发布。
