# V0.4.5 快速上手

这份指南面向第一次接触项目的用户，目标是先构建并运行一个小型端到端示例，再说明怎样继续使用图搜索负载和统一命令行。

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

## 构建并运行教学示例

以下命令从仓库根目录执行。它把 Python 包和示例输出放在一个新的临时目录中，避免写入系统 Python 或覆盖已有结果。`mktemp` 在 macOS 上返回的目录会在命令结束后仍保留，便于查看输出。

```sh
set -e
OUT="$(mktemp -d /tmp/ndpsim-quickstart-XXXXXX)"
PYTHON=python3.13

# 将公共 Python 命令行安装到独立目录
cp -R ./python "$OUT/python-src"
"$PYTHON" -m pip install --target "$OUT/python" "$OUT/python-src"
export PYTHONPATH="$OUT/python"
"$PYTHON" -m ndpsim --help

# 独立构建真实块求和程序；这一步只依赖 Capture SDK
cmake -S examples/extension -B build/block-sum-native \
  -DNDPSIM_SDK_ROOT="$PWD/sdk"
cmake --build build/block-sum-native -j2

# 构建 SST/MQSim 模拟后端和扩展示例模块
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2

# 真实执行、采集、独立语义核对，并运行 Host/Controller 两种映射
"$PYTHON" examples/extension/tutorial.py \
  --native "$PWD/build/block-sum-native/block_sum_native" \
  --device "$PWD/configs/devices/native-small.xml" \
  --build "$PWD/build/delivery" --output "$OUT/tutorial"

# 再用通用 CLI 检查 IR 包结构
"$PYTHON" -m ndpsim check \
  --package "$OUT/tutorial/package" \
  --registry examples/extension/operators.json \
  --report "$OUT/check.json"

echo "结果目录：$OUT"
```

若 `python3.13` 不在 PATH 中，请把 `PYTHON` 改为与锁定依赖匹配的 Python 可执行文件。第一次构建可能需要较长时间；后续可重复运行构建命令增量更新。每次重新运行教程时，`--output` 必须指向一个不存在的目录；重新执行时可重新运行 `mktemp` 那一行创建新的 `OUT`。

成功时教程会输出 `teaching_passed: true`，并给出两种映射的模拟摘要。示例默认输入为 1 到 8 的八个 u32，两个分支分别得到 10 和 26，汇合结果为 36。可在 `$OUT/tutorial/` 查看：

- `semantic.json`：独立 Python oracle 对真实计算结果、访问、Work 和依赖的核对。
- `structural.json`：静态 IR 包结构检查报告。
- `host.jsonl`、`controller.jsonl`：模拟的逐事件轨迹；`comparison.json` 汇总两种放置。
- `native-command.json`、`host-command.json`、`controller-command.json`：实际执行命令、退出码和输出。
- `$OUT/check.json`：`ndpsim check` 生成的通用结构检查报告。

这是教学 fixture：成本参数是手工设定的假设，SSD/PCIe/Controller 仍为 `configured_unvalidated`。`full_acceptance_passed` 会保持 `false`。模拟时间不能用来推断真实硬件速度或下沉收益；该示例也不替代正式 SIFT1M 精度验证。

## 接下来怎么用

统一 CLI 的主要工作流是：

1. `ndpsim capture`：调用独立负载适配器，采集完整静态 IR。
2. `ndpsim check`：检查包结构；需要时使用显式适配器和参考结果做语义／Work 核对。
3. `ndpsim fit`：从独立测量拟合 CPU 成本 Profile，或构建逐调用服务表。
4. `ndpsim simulate`：使用显式配置和已构建后端运行 timing-only 模拟。
5. `ndpsim compare` / `ndpsim verify`：分别做绑定报告的描述性比较和分层验证。

命令细节、输入格式、预算和退出码见[统一 CLI 使用说明](../python/CLI.md)。如果要复现项目现有正式负载，请先看[图搜索采集指南](../workloads/graph-search/CAPTURE.md)；该流程需要准备锁定的 SIFT1M 数据和 NSG 图，数据不随仓库分发。要扩展算子和映射，从[教学扩展示例](../examples/extension/README.md)开始。完整干净目录构建和正式发布证据见[V0.4.5 发布验收](releases/v0.4.5.md)。
