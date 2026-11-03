# V0.3 归档检查

2026-11-03（Asia/Shanghai）。本次仅整理 V0.3 历史上下文，不实施 V0.4 功能，不改变 V0.3 总交付结论。输入为本次工作区、已有 `build/delivery` 和锁定 SST 缓存；未执行 Git 提交、依赖更新或初始化。

## 保全与静态核对

- 8份阶段文档迁入本目录，相对链接同步更新。V0.3 PRD、IR总规范和正式发布记录保留原入口，现行IR接口仍按文档索引选读。
- 工具与配置仅归档版本说明，保留原实现、查询/预算/矩阵锁、配置参数和测试路径。文档检查器只更新计划路径，未放宽断言。
- 本机结果实体移至 `results/archive/v0.3/`，原 `results/v0.3` 为相对兼容链接；全部15,970个文件、10,965,643,691字节迁移前后逐文件SHA256一致。无内部符号链接需要重定向；失败样本与manifest均保留，未压缩或删除。
- 受保护文件核对覆盖源码、工具、配置、tests、schema、examples、根CMake和依赖锁；仅允许文档检查器的上述路径变化。Markdown链接、Python语法和正式交付文档检查另有机器报告。缺失的本机历史证据链接单列，不视为通过证据。

## 验证入口与边界

实际命令、退出码、耗时和日志保存在本机忽略目录 `results/v0.4/archive-v03-20261103/` 的 `commands.json`：

```sh
cmake -S . -B build/delivery -DBUILD_TESTING=ON
cmake --build build/delivery -j2
ctest --test-dir build/delivery --output-on-failure
```

本轮使用已有缓存重建，不证明干净SST复建。CTest包含的小矩阵不等同完整AC；未重跑完整V0.1/V0.3验收、全规模SIFT矩阵、交替效率/Q梯度、独立原生oracle或完整ASan验收，也未进行V0.4采集或精度验证。

`initial-status.txt`、`moves.json`、`protected-before.json`、`results-before.json`、`results-check.json`、`static-checks.json` 和最终 `final.json` 保存归档身份、迁移映射、断言及hash。历史成功缓存仍须经过原有严格身份核验；归档不使历史结果变为当前源码验收通过。

实际结果：根配置、编译和CTest退出码均为0，82/82测试通过（24.27秒）。195个受保护文件中仅文档检查器路径发生预期变化；874处相对链接扫描无源码/文档目标缺失，81处未随检出提供的本机历史证据单列。工具Python语法与正式文档检查通过。
