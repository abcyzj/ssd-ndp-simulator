# MaxSim 合成负载与 E0～E5 执行

输入 [实验方案](../../docs/MaxSim_ndp.md) 的参数，生成从查询向量和候选 ID 就绪到 Host Top-K 就绪的语义 DAG。产物可用 V0.4.5 Python SDK 校验，无需构建 SST/MQSim 或准备向量文件。来源为 `synthetic-fixture`，证据覆盖结构、依赖与账本。算子输出仅有字节数和元数据摘要。

新实验从 [9550 PRO 重跑手册](#micron-9550-pro-候选设备与重跑) 开始。下文的 `local-10k.json` 和 `e0.4.json` 命令用于复现历史夹具。

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

[local-10k.json](local-10k.json) 复现历史一万页 E0，并绑定 [8 GiB raw 设备 XML](local-10k-device.xml) 的 SHA-256 和 MQSim commit。历史夹具使用 28 GB/s 配置链路、16 条 3600 MT/s NAND 通道和 `fetch=1`。一万文档 FP16 Layout 占前 12,800,000 个扇区，需预装载 400,000 个 16 KiB NAND 页。

## 复现历史 E0 夹具

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

`e1.py` 运行一万文档 E1 扫描。新批次显式传入 `--config workloads/maxsim/micron-9550-pro.json`。每个分布和种子共用候选 IR。主机两条路径使用同一个预热快照，分别按实际读完成和命中事件更新 LRU。盘侧全量下沉复用同一分布、种子和并发下的零缓存结果。每个查询完成后立即补发，读取以文档为单位在查询间轮转，评分批仅在同一查询内组合。

用重复的 `--point` 和 `--seed` 选择扫描点和种子。默认 10,000 查询一窗的 LRU 重放只用于预热。用 `--cold` 单独跑空缓存起点。测量 QPS 按整段有限闭环完成量计算，避免短窗口恰好落在高并发完成批次之间。短运行的 P99 只作描述。原始轨迹保留每个查询的开始、可见和排空时间，以及 NAND 利用率和资源汇总。完整逐事件对拍使用 E0 入口。

## E2 开放到达实验

`e2.py` 复检 E1 的一万页候选包，并复用同组预热快照。每组放置共享同一独立随机流生成的泊松到达时刻。测量从预装载排空开始，查询按到达顺序准入，同时最多执行 16 个。入口等待从到达计到准入，精排延迟从到达计到 Top-K 可见。

按重跑手册将 `--e1` 指向同一批次的 E1 结果。默认压力点为 10/30/100/300/1000 QPS，加上从新 E1 吞吐估计派生的探针。可重复使用 `--scenario`、`--rate` 和 `--seed` 选择子集。在最后一次到达时截取完成数、入口排队和在途数，随后排空以复检资源和流量。窗口内延迟仅对后半段到达且在截点前完成的查询统计。持续积压需结合增长、入口等待和种子间变化判断。

## 混合放置 `hybrid_miss_only`

查询准入时按主机 LRU 快照逐候选分流。命中项更新 LRU 顺序，通过主机侧的暂存和评分服务。未命中项等下沉命令到达后走 MQSim 本地读、盘控暂存和盘控评分。快照成员在运行中固定，重复未命中按各次查询分别读取。两侧各有一个评分引擎和独立暂存预算，读取沿用全局 QD 和每查询读窗口。

有 `M` 个未命中时，PCIe 上每查询有一条命令和一条结果。命令有效载荷为查询矩阵字节加 `8 × M`，结果有效载荷为 `12 × M`，另计消息头和 TLP 开销。主机命中可在命令飞行期间评分。全命中时没有下沉消息。统一 Top-K 等待主机评分和盘控结果全部可见。

单查询可用 `run.py --placement host --placement hybrid_miss_only --cache-snapshot snapshot.json` 核验细粒度轨迹。快照 JSON 采用 `{"host_capacity_bytes": 1310720, "cache_oldest_first": [42, 17]}`，ID 按最旧到最新排列，容量按有效向量字节计。省略快照时单查询使用零缓存。闭环使用 E1 预热生成的配对快照。按方案 §7.2 运行 9550 PRO 首个种子的先导点，复用重跑手册中的 `profile`、`build`、`budget` 和 `backend`：

```sh
"$maxsim_python" workloads/maxsim/e1.py --config "$profile" --build "$build" \
  --output "$run_root/hybrid-pilot" --backend-source-sha256 "$backend" --budget "$budget" \
  --queries 144 --seed 11 --workers 1 --point E1-boundary-43 --point E1-boundary-44 \
  --placement host --placement hybrid_miss_only --placement device_all \
  --nvme-read-die-admission --local-read-die-admission
```

上述两点为 Zipf 0.8、20.97152% 缓存覆盖率、并发 16 和 64。普通读 Die 准入应用于 `host`，本地读 Die 准入应用于 `hybrid_miss_only` 和 `device_all`。E2 也可通过重复 `--placement` 选择同组放置并共享到达序列。两种负载的汇总按侧报告评分数、批次、计算时间、利用率和暂存峰值，同时记录协议载荷。对于混合方案，两侧总计算时间可以重叠，资源占用分别看 `host_scoring_utilization` 和 `controller_scoring_utilization`。结果适用范围为精排子系统和 `configured_unvalidated` 设备模型。

## Micron 9550 PRO 候选设备与重跑

[`micron-9550-pro.json`](micron-9550-pro.json) 是本轮 E0～E5 的设备入口。它绑定 [XML](micron-9550-pro-device.xml) 和 MQSim fork 身份。主候选使用 Gen5 ×4、256 B MPS、设备队列 1024、`fetch=128`、16 条 2400 MT/s 通道、每通道 4 chip、每 chip 2 die、每 die 4 plane、TLC、55 µs 等效阵列读、8 MiB CMT 和 128 GiB raw。全程使用 64 GB/s 盘控暂存。设备状态为 `configured_unvalidated`。

**首轮证据。** 主候选的 4 KiB 随机 QD1 P50 为 57.3 µs，128 KiB 顺序 QD32 为 14.13 GB/s，4 KiB 随机 QD512 为 0.85 MIOPS。对应规格为 60 µs、14 GB/s 和 3.30 MIOPS。8 chip/channel、`fetch=512` 的诊断变体达到 1.63 MIOPS。E0、E1～E5 代表点和 83 项 CTest 均通过。盘内本地读与下沉收益仍属条件预测。首轮本机证据在 `results/maxsim/micron-9550-pass1.tNLrbz/pass1-summary.json`，可按下列命令生成新证据。 [Micron 9550 技术规格](https://www.micron.com/content/dam/micron/global/public/products/data-sheet/ssd/9550-nvme-ssd-tech-prod-spec.pdf)

### 1. 构建、普通 NVMe 校准和 E0

```sh
set -e
export PYTHONPATH="$PWD/python:$PWD/workloads/maxsim"
maxsim_python="$PWD/.venv/bin/python"
profile="$PWD/workloads/maxsim/micron-9550-pro.json"
build="$PWD/build/delivery"
mkdir -p results/maxsim
run_root=$(mktemp -d "$PWD/results/maxsim/micron-9550-rerun.XXXXXX")
cmake -S . -B "$build" -DBUILD_TESTING=ON -DPython3_EXECUTABLE="$maxsim_python" \
  > "$run_root/configure.log" 2>&1
cmake --build "$build" -j2 > "$run_root/build.log" 2>&1
backend=$("$maxsim_python" -c 'from run import backend_digest; print(backend_digest())')
budget="$run_root/budget.json"
cat > "$budget" <<'JSON'
{"rss_bytes":4294967296,"output_bytes":536870912,"scratch_bytes":536870912,"min_disk_free_bytes":1073741824,"timeout_seconds":180}
JSON
ctest --test-dir "$build" --output-on-failure > "$run_root/ctest.log" 2>&1
"$maxsim_python" workloads/maxsim/calibrate.py --config "$profile" --build "$build" \
  --budget "$budget" --output "$run_root/nvme" --seed 11 \
  --fetch 64 --fetch 128 --fetch 256 --fetch 512
"$maxsim_python" workloads/maxsim/generate.py generate --config "$profile" \
  --point E0-single-01 --seed 11 --output "$run_root/e0-input"
"$maxsim_python" workloads/maxsim/run.py --input "$run_root/e0-input" --build "$build" \
  --budget "$budget" --backend-source-sha256 "$backend" --output "$run_root/e0"
```

核对 `nvme/matrix.json` 中 `fetch=128` 的三项规格偏差和 64 KiB 随机读。核对 `e0/execution.json` 中三种放置的成功与排空状态，并用日志和输入账本对照每查询 1,500 个读请求、98,304,000 B 有效向量和 3,145,728,000 FLOP。Host 应收到完整文档向量，`device_all` 的向量 D2H 为 0 B。示例 E0 使用配置的默认 Zipf 0.8 轨迹，首轮证据中的 E0 使用均匀候选。若设备配置改变，重新生成输入并重复这两步。

### 2. 运行 E1，再用新结果选 E2

```sh
df -h "$run_root"
"$maxsim_python" workloads/maxsim/e1.py --config "$profile" --build "$build" \
  --output "$run_root/e1" --backend-source-sha256 "$backend" \
  --queries 144 --seed 11 --seed 23 --seed 47 --workers 1
# 检查 e1/matrix.csv 后执行。以下运行默认的三个代表场景。
"$maxsim_python" workloads/maxsim/e2.py --build "$build" --e1 "$run_root/e1" \
  --output "$run_root/e2" --workers 1
```

E1 可通过 `--reuse-candidates` 对照旧批次。设备不同会生成绑定新配置的包，候选序列和逻辑读清单必须与旧包哈希一致。E2 接收 E1 输出目录，用对应容量、种子和局部性下的吞吐估计生成加密到达探针。可用 `--scenario` 和 `--capacity-bytes` 选取新的 E1 代表点，用 `--rate` 固定探针。每个 E1 输入包至少需 16 次查询以覆盖 E2 的活动查询配额。

根据新 E1 的配对吞吐和延迟确认收益区间，再决定默认三个场景是否适合 E2。选择其他容量时逐个指定场景和容量，为每组设置独立输出目录。可用 `--point` 缩小 E1 首次探针，并保留足够查询供 E2。E1 和 E2 都使用固定的 180 秒单进程超时。完整采集前先用代表点核对墙钟、RAM、磁盘和输出预算。

### 3. 运行 E3～E5

```sh
for batch in E3 E4 E5; do
  "$maxsim_python" workloads/maxsim/sweep.py "$batch" --config "$profile" --build "$build" \
    --output "$run_root/$batch" --budget "$budget" --concurrency 16 \
    --seed 11 --seed 23 --seed 47 --queries 48
done
```

- E3 的 `baseline` 是 **15.75384 GB/s**。运行器把历史扫描标签中的 7 GB/s 档映射为 **7.87692 GB/s**，把旧 14 GB/s 档并入 `baseline`，保留 **28 GB/s** 假设链路点。默认覆盖 Zipf 0.8 和 Zipf 1.2，可用 `--scenario` 筛选。
- E4 与 E1 共用设备几何。十万文档点 `E4-representation-24` 需要预装载 **400 万 NAND 页**。先单点测量墙钟、RSS 和输出，按实际资源需要调整重跑预算。
- E5 保持 `fetch=128`，分别扫描应用层在途读上限和设备队列深度。`device-queue-256` 是设备队列对照，应用层 QD 点仍由 `scan.json` 展开。

同一批次保存命令和退出码、种子、XML 和 IR 哈希、实际配置、SST/后端构建身份、预热和测量窗口以及排空断言。失败样本留在原目录，用新路径重跑。报告标记 `configured_unvalidated`，并列低 QD、顺序读和高 QD 校准覆盖。短样本 P99 只作描述。

## 盘内整页读微基准

微基准使用 9550 PRO 候选 XML 和 CWDP 排列，由盘控在 NVMe 预装载并排空后提交 16 KiB 对齐页读。先只读核对物理地址，再逐笔对上 TSU 排队、PHY 派发、NAND 区间和盘控完成。A/B 使用同一地址序列，每 die 固定一个 plane，每笔完成即补页。默认 A 沿用 chip 级准入，B 允许读忙 chip 的空闲 die 接收盘内页读。直收测量终点为 NAND 数据到达盘控，另有 64 GB/s 暂存端口对照。

```sh
mkdir -p results/maxsim
run_root=$(mktemp -d "$PWD/results/maxsim/nand-local-read.XXXXXX")
cat > "$run_root/budget.json" <<'JSON'
{"rss_bytes":4294967296,"output_bytes":268435456,"scratch_bytes":67108864,"min_disk_free_bytes":1073741824,"timeout_seconds":120}
JSON
PYTHONPATH="$PWD/python:$PWD/workloads/maxsim" .venv/bin/python \
  workloads/maxsim/local_read_probe.py --config workloads/maxsim/micron-9550-pro.json \
  --build build/delivery --budget "$run_root/budget.json" --output "$run_root/matrix"
```

先用根 CMake 构建 `ndpsim_maxsim`。可重复传入 `--case` 筛选对照。全矩阵的单通道点各发 1,024 页并预热 128 页，全通道点各发 8,192 页并预热 1,024 页。每次运行预装载 4,096 页，循环访问预装载的 plane 0 页。测量窗口从预热完成时刻到停止补发的最后一次提交之前，排除预热结束时刻的完成，计入截止时刻提交前已经释放的完成。之后单独排空。验证器要求预装载后直到排空的 Host PCIe 事件数为零，并独立对账请求、NAND 页及字节。

单通道的单页基线测得命令 0.290 µs、阵列 55.020 µs、数据传输 6.827 µs。同 die 从 QD1 提到 QD2 后，吞吐仍为 0.264 GB/s。关键组的同 chip 双 die 阵列重叠从 0 增至 24.464 ms，吞吐从 0.264 增至 0.475 GB/s。不同 chip 的正对照在 A/B 均为 0.475 GB/s，均出现 24.464 ms 阵列重叠。所有点的同通道命令与数据传输重叠为零。

| 全盘在途页 | 盘控有效 GB/s A → B | 通道 data-out 占比 A → B | 页读 P95 µs A → B |
| ---: | ---: | ---: | ---: |
| 16 | 4.219 → 4.219 | 10.99% → 10.99% | 62.1 → 62.1 |
| 64 | 12.692 → 12.692 | 33.05% → 33.05% | 82.6 → 82.6 |
| 128 | 12.692 → 19.078 | 33.05% → 49.68% | 165.2 → 109.9 |
| 256 | 12.692 → 19.078 | 33.05% → 49.68% | 330.5 → 219.9 |

QD128 的 A 在通道空闲时累计 381.216 ms 的排队请求时间，期间目标 die 空闲、同 chip 的另一 die 有在途服务。B 的同口径为零。此指标对请求累加，单位为页·时间。B 的阵列完成至 data-out 开始等待 P95 为 45.759 µs。每条通道的 data-out 呈八页连续突发，突发间隔中有 55.310 µs 无 data-out。八次传输加上这段空档形成 109.926 µs 的周期，可重算 19.078 GB/s。继续增加 QD 到 256 只增加等待时间。下一步可在同样的单 plane 设置下测试错开各 die 的供给相位，观察能否填补突发之间的空档，并核对 data-out 优先级是否延迟下一波命令。

`matrix.json` 保存逐通道利用率、等待统计、双 die 重叠、吞吐和排空指标。各点目录保留输入哈希、命令、退出码、物理页地址和原始时间区间。结果适用于这组确定性整页负载与 `configured_unvalidated` 设备模型。

### 全盘暂存与启动错峰

用同一个预算和已有构建分别跑直收与暂存，复用同一页清单和 NAND 完成后补发规则。暂存采用 [MaxSim 执行配置](e0-run.json)的 64 B/ns 和现有单槽 FIFO。每页 NAND 完成后提交一次暂存写入，服务耗时 256 ns。统计使用同一个 NAND 稳态窗口，并在停止补发后单独验证 NAND 与暂存全部排空。

```sh
export PYTHONPATH="$PWD/python:$PWD/workloads/maxsim"
for endpoint in raw staging; do
  set --
  if [ "$endpoint" = staging ]; then set -- --staging; fi
  .venv/bin/python workloads/maxsim/local_read_probe.py \
    --config workloads/maxsim/micron-9550-pro.json --build build/delivery \
    --budget "$run_root/budget.json" --output "$run_root/$endpoint" \
    --case all-qd16 --case all-qd64 --case all-qd128 --case all-qd256 \
    --seed-gap-ns 7000 --seed-gap-ns 7768 "$@"
done
```

四档 QD 的 A/B 暂存后带宽与上表的直收带宽一致。B 在 QD128 和 QD256 均为 19.078 GB/s，暂存端口占用率 29.81%，暂存排队 P95 为 3.840 µs，计入服务后为 4.096 µs。直收和暂存的 NAND 页级账本哈希逐点相同。暂存验证覆盖每页 FIFO 顺序、服务时间和字节守恒。

两个启动错峰对照只改变 QD128 B 的首批请求种子。各通道按 chip/die 顺序铺开，形成 8 批，每批 16 条通道各发一页；批次间隔分别为 7.000 和 7.768 µs。初始队列填满后恢复完成即补发。在通道 0，7.768 µs 点首轮是 8 次单页回传，随后从预装载结束后的 148.519 µs 起重新出现 8 页突发。7.000 µs 点在 262.780 µs 出现首个 8 页突发。两点的稳态 data-out 占比仍为 49.68%，突发间隔仍为 55.310 µs。

7.768 µs 点的通道 0 第一笔补发在 62.137 µs 排队，至 93.209 µs 派发。首笔回传结束后，总线曾有 0.941 µs 空闲间隙，单页命令只需 0.290 µs。PHY 在同 chip 仍有阵列活动时保留 `DATA_OUT` 状态，当前 B 只对 `READING` 和 `WAIT_FOR_DATA_OUT` 开放空闲 die。这个状态门控与轨迹吻合，适合作为下一轮独立 A/B 的改动点。

### 完整 MaxSim 的调度 A/B

对同一个 E0 包分别运行两次 `run.py`。默认使用 chip 级准入。传入 `--nvme-read-die-admission` 为 `host` 和 `host_compute_free` 启用跨 die 调度，传入 `--local-read-die-admission` 为 `device_all` 启用。两个开关可以独立使用，也可以在一次包含三种放置的执行中同时使用。普通读仍通过 NVMe 提交和完成。开关在预装载排空后、DAG 启动前生效，轨迹头记录每种放置实际启用的开关。

E1 定向对照复用同一个闭环候选包，向 `e1.run_point` 传入并发度以及对应的 `nvme_read_die_admission` 或 `local_read_die_admission`。零缓存对照使用容量 0 和空快照。E1 的 NAND GB/s 使用所有 NAND 读字节除以预装载后至排空的模拟时间，查询 QPS 使用到最后一个 Top-K 可见的有限闭环窗口，P95 使用从查询进入闭环到 Top-K 可见的时延。

9550 PRO 候选的定向验证采用种子 11、Zipf 0.8 和 144 个 E1 查询，E0 使用同序列首查询。E0 A/B 的 6,000 个 NAND 页事务、98,304,000 B 有效输入和 3,145,728,000 FLOP 一致。E1 并发 1 的 NAND 带宽为 11.96 → 17.12 GB/s，QPS 为 121.71 → 174.19，P95 为 8.57 → 6.01 ms。并发 64 的对应结果为 12.80 → 18.76 GB/s、130.25 → 190.89 QPS 和 493.56 → 336.88 ms。结果标记为 `configured_unvalidated`。

## 扫描与随机种子

[scan.json](scan.json) 声明 E0～E5 的轴。E1 展开容量、分布和封闭并发的指定组合。E2 的默认探针是零缓存均匀、5.24288% Zipf 0.8 和 20.97152% Zipf 1.2，收益区间由新 E1 确认。E3～E5 为单因素扫描，链路取值以重跑手册的执行口径为准。扫描输出包括点 ID、覆盖项、种子组和派生账本。预检报告候选、切片、stage、IR/SQLite 估计和磁盘预算，主配置直接生成 10,000 查询包会触发默认预算拒绝。

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
