# SSD NDP Simulator

V0.4.5 开发分支提供静态 IR 的 timing-only 模拟。原生图搜索程序采集访问、Work 和依赖，模拟器使用这些元数据调度服务。支持 Host 内存驻留，以及单 SSD 上的 Host 和 Controller 放置。当前正式交付记录为 [V0.4](docs/releases/v0.4.md)，V0.4.5 的任务和验收状态见[实施清单](docs/implementation-plan-v0.4.5.md)。

## 构建和运行

```sh
cmake -S . -B build/timing -DBUILD_TESTING=ON
cmake --build build/timing -j2
ctest --test-dir build/timing --output-on-failure
python3 -m pip install ./python
ndpsim --help
```

根构建使用锁定 SST Core 和 MQSim fork。SST 默认从共享缓存准备，也可显式设置 `SST_ROOT`；依赖版本和构建来源见 [deps.lock.json](deps.lock.json) 和 [MQSim 维护说明](docs/mqsim-source-management.md)。`BUILD_TESTING=OFF` 可构建模拟模块；MQSim 独立对拍需要启用测试构建。

图搜索参考程序有自己的构建入口，仅需 Capture SDK：

```sh
cmake -S workloads/graph-search -B build/graph -DNDPSIM_SDK_ROOT="$PWD/sdk"
cmake --build build/graph -j2
```

- [统一 CLI](python/CLI.md)覆盖采集、校验、拟合、模拟、比较及三层验证。
- [图搜索采集](workloads/graph-search/CAPTURE.md)说明原生输入、完整 IR/2 和测量。
- [教学扩展](examples/extension/README.md)提供从真实块求和采集到两种 SSD 放置的夹具。
- [配置入口](configs/README.md)说明运行时 XML、Layout 和测试配置。
- [V0.4.5 PRD](docs/prd-v0.4.5.md)说明支持范围。SSD、PCIe、Controller 为 `configured_unvalidated`。

## 迁移与历史复现

旧 direct、动态 IR、Replay 配置及包须迁移。使用真实业务程序重新采集完整 IR/2，并按当前契约配置 Layout、Profile、设备和放置。历史 V0.4 命令请在 V0.4 交付源码 `8cf48fc` 上按发布记录复现。保留的历史文档及源码路径描述对应其记录版本。 [文档索引](docs/README.md)
