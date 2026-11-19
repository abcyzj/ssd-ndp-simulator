# MaxSim 合成语义 IR 生成器

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
python workloads/maxsim/generate.py describe --point E0-single-01
python workloads/maxsim/generate.py generate --point E0-single-01 --seed 23 --output /tmp/maxsim-e0-seed23
```

[scan.json](scan.json) 声明 E0～E5 的轴。E1 展开容量、分布和封闭并发的指定组合。E3～E5 为单因素扫描。E2 的开放压力点可生成到达序列，选择哪些 E1 点作性能对照须待执行结果。扫描输出包括点 ID、覆盖项、种子组和派生账本。默认生成 1 个查询，实验建议的 10,000 查询记录在 `measured_queries_initial`。预检报告候选、切片、stage、IR/SQLite 估计和输出磁盘预算。默认预算会拒绝将主配置直接生成成 10,000 查询包。

`layout_seed`、`candidate_seed`、`arrival_seed` 分用三个带域标识的随机流。`--seed` 为三路设置同一组种子，配置文件可分别控制。固定置换将热度排名映射到文档 ID。均匀组直接无放回抽样。Zipf 组使用累计权重抽样并拒绝重复，连续拒绝 64 次后用剩余排名的指数竞赛继续按相同分布抽样。开放模式输出累计指数间隔上取整到 ns 的到达时间，封闭模式保留完成后补发规则及并发度。用相同的 Python 构建、生成器源码、配置、种子复现包身份。

## 产物

| 文件 | 用途 |
| --- | --- |
| `effective-config.json` | 展开配置、参数来源、单位字段、派生账本和资源估算。缓存、成本、调度、负载和三条执行方案的参数供后续接入使用。 |
| `candidates.jsonl` | 每查询的去重文档 ID，按抽样顺序。 |
| `logical-layout.json` | 偏移为 `doc_id × stride`，步长为文档有效大小按 4 KiB 向上取整。含语料逻辑容量下限和排名映射身份。 |
| `layout.json` | 同时显式给出 `layout.namespace_capacity_sectors` 和 `layout.namespace_page_bytes` 时导出 `ndpsim-layout/1`。配置中的 MQSim XML 可按相对路径引用并核验 SHA-256。 |
| `logical-reads.jsonl` | 每个候选的顺序、有效切片和按 4 KiB 补齐的全 miss 请求长度。NAND 页分解及链路流量由执行侧计量。 |
| `arrivals.jsonl` | 仅开放模式生成。每查询一行，单位为 ns。 |
| `scan-definition.json`、`operators.json` | 扫描定义及版本化算子注册表。可变输出规则绑定实现哈希，通过本生成器的 `check` 入口复检。 |
| `package/` | 标准 IR manifest、operations、index 和 tail。每文档的评分等待读取全部有效切片，Top-K 等待全部候选分数。 |
| `structure.json`、`accounting.json`、`receipt.json` | SDK 校验、独立账本校验和整次生成的发布凭据。 |

主配置每查询记录 **98,304,000 B** 有效文档输入、**3,145,728,000 FLOP**、**1,500** 个 64 KiB 全 miss 逻辑读，以及 **21,680 B** 下发、**1,800 B** 返回的有效负载。655.36 GB 语料只占一个逻辑对象。三条路径共用语义包。动态缓存命中、批次、读窗口、反压、协议服务、设备预装载和吞吐指标留到运行时接入。

## 首批验收命令

```sh
PYTHONPATH=python python workloads/maxsim/test_maxsim.py
```

用例覆盖主配置账本与双输出复现、模型和 E0～E5 展开、FP32 及模板增量、独立随机流、开放到达、全候选覆盖、尾部补齐、显式 Layout、未来执行参数传递、容量和输出预算，以及非法访问/Work/依赖/输出和失败发布。
