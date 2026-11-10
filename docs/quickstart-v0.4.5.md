# V0.4.5 快速上手

这份指南面向第一次接触项目的用户，先通过统一 CLI 跑通块求和，再用相同的命令观察图搜索的依赖、成本和流量。

## 项目做什么

SSD NDP Simulator 是一个 timing-only 仿真平台：它读取真实程序采集并校验过的静态 IR 元数据，根据操作依赖、工作量、数据访问、硬件配置和成本模型模拟服务时间与资源竞争。仿真阶段不读取业务 payload，也不重新计算业务结果。

第一次运行建议从块求和教学扩展示例开始。它会真实执行并采集三个操作，独立核对结果和 Work，然后在相同输入包上分别运行 SSD Host 与 Controller 放置。示例很小，不需要下载 SIFT1M 数据集。

## 运行环境

V0.4.5 发布记录实际验证的平台是 macOS 26.5.1 arm64；其他系统或架构尚未验证。需要仓库锁定版本对应的 Xcode Command Line Tools、CMake、Python 3.13、`python-config`、autoconf、automake、GNU libtool、m4、make。Python 安装还需要 setuptools 77 或更新版本、jsonschema 和 referencing。依赖身份见 [deps.lock.json](../deps.lock.json)。

首次获取源码时请带上子模块：

```sh
git clone --recurse-submodules <repository-url> SSD_NDP_Simulator
cd SSD_NDP_Simulator
```

根 CMake 会准备并构建锁定的 SST Core 和 MQSim。初次构建需要网络可访问锁定源码，并预留构建磁盘空间；`NDPSIM_SST_CACHE_DIR` 可用于指定 SST 缓存目录。

## 构建与安装

在仓库根目录执行以下命令。两个课程使用同一个 Python 安装和模拟构建。将 `PYTHON` 改为与锁定依赖匹配的解释器路径。每次重跑课程时创建新的 `OUT`，命令要求输出目录尚不存在。

```sh
set -e
OUT="$(mktemp -d /tmp/ndpsim-quickstart-XXXXXX)"
PYTHON=python3.13
cp -R ./python "$OUT/python-src"
"$PYTHON" -m pip install --target "$OUT/python" "$OUT/python-src"
export PYTHONPATH="$OUT/python"
ndp() { "$PYTHON" -m ndpsim "$@"; }

BUILD="$PWD/build/delivery"
cmake -S . -B "$BUILD" -DBUILD_TESTING=ON
cmake --build "$BUILD" -j2
```

两课的 `compare` 都使用下列函数生成输入清单，绑定两份成功模拟报告及其 SHA-256。

```sh
compare_spec() {
  "$PYTHON" - "$@" <<'PY_SPEC'
import sys
from pathlib import Path
from ndpsim.execution import save
from ndpsim.static_ir import file_hash

spec, *reports = map(Path, sys.argv[1:])
save(spec, dict(format='ndpsim-report-comparison/1',
                runs=[dict(path=str(p.resolve()), sha256=file_hash(p)) for p in reports]))
PY_SPEC
}
```

## 第一课 · 块求和

默认输入是八个 u32。原生程序真实读取前后各 16 B，得到 `10` 和 `26`，最后汇合为 `36`。两个分支在 IR 中可以同时就绪，汇合等待两条数据依赖。

```sh
SUM="$PWD/workloads/block-sum"
ndp capture --workload block-sum --adapter "$SUM/block_sum_capture.py" \
  --native "$BUILD/workloads/block-sum/block_sum_native" \
  --operators "$SUM/operators.json" --config "$SUM/capture.json" --output "$OUT/sum"

SUM_CAPTURE="$OUT/sum/capture"
ndp check --adapter "$SUM/block_sum_capture.py" --package "$SUM_CAPTURE/package" \
  --registry "$SUM_CAPTURE/registry.json" --semantic-config "$SUM/capture.json" \
  --reference "$SUM_CAPTURE/semantic.json" --report "$OUT/sum-check.json"

for placement in host controller; do
  ndp simulate --config "$SUM_CAPTURE/$placement-experiment.json" \
    --build "$BUILD" --output "$OUT/sum-$placement"
done
compare_spec "$OUT/sum-compare.json" "$OUT/sum-host/report.json" "$OUT/sum-controller/report.json"
ndp compare --spec "$OUT/sum-compare.json" --output "$OUT/sum-comparison"
```

先看 `sum-check.json` 的 `expected` 和 IR 包里的 `operations.jsonl`，再对照两份模拟的 `stages.csv` 和 `report.json`。在报告的 `drain` 中，Host 的两次 sector 读取共计 1024 B 输入 DMA，Controller 本地读的输入 D2H 为 0。Controller 返回及排空事件见 `trace.jsonl`。两次模拟使用同一份 IR，模拟只消费元数据。

## 第二课 · 图搜索

小型合成输入可以直接生成。沿用四条命令，替换原生程序、适配器和运行配置即可。

```sh
GRAPH="$PWD/workloads/graph-search"
ADAPTER="$GRAPH/python/graph_capture.py"
"$PYTHON" "$GRAPH/python/make_fixture.py" --output "$OUT/graph-input"
GRAPH_CONFIG="$OUT/graph-input/config.json"

ndp capture --workload graph --adapter "$ADAPTER" --native "$BUILD/ndpsim_native" \
  --operators extensions/graph/operators.json --config "$GRAPH_CONFIG" --output "$OUT/graph"

GRAPH_CAPTURE="$OUT/graph/capture"
ndp check --adapter "$ADAPTER" --package "$GRAPH_CAPTURE/package" \
  --registry "$GRAPH_CAPTURE/registry.json" --semantic-config "$GRAPH_CONFIG" \
  --reference "$GRAPH_CAPTURE/reference.json" --allow-fixture --report "$OUT/graph-check.json"

"$PYTHON" "$GRAPH/python/teaching_ssd.py" --capture "$GRAPH_CAPTURE" \
  --device configs/devices/native-small.xml --output "$OUT/graph-experiments"
for placement in host controller; do
  ndp simulate --config "$OUT/graph-experiments/$placement-experiment.json" \
    --build "$BUILD" --output "$OUT/graph-$placement"
done
compare_spec "$OUT/graph-compare.json" "$OUT/graph-host/report.json" "$OUT/graph-controller/report.json"
ndp compare --spec "$OUT/graph-compare.json" --output "$OUT/graph-comparison"
echo "结果目录 $OUT"
```

这次可以从 `operations.jsonl` 追踪每个查询的邻接读取、候选打分与选择，看各阶段的 Work 如何随查询变化。再在 `services.csv` 和 `report.json` 对照算力需求和分层字节，观察 Controller 路径如何在本地读取向量并返回结果。

两课使用手工教学成本，硬件状态为 `configured_unvalidated`。比较报告描述本次配置的模拟行为。正式 SIFT1M 数据、采集与精度验证另见[图搜索采集指南](../workloads/graph-search/CAPTURE.md)及[V0.4.5 发布记录](releases/v0.4.5.md)。扩展算子与映射见[块求和实现说明](../workloads/block-sum/README.md)，参数与预算见[统一 CLI 使用说明](../python/CLI.md)。
