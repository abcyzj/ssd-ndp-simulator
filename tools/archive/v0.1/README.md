# V0.1 阶段复现工具归档

这些脚本保留阶段构建、PoC、分层验收逻辑，不作为当前任务指令。脚本根路径和共享模块导入已修正，可从任意工作目录通过脚本绝对路径调用；参数中的相对路径仍按调用目录解释。当前默认入口见[工具索引](../../README.md)，回归说明见[测试文档](../../../docs/testing.md)。

归档不表示断言无效；持续 CTest 和完整 AC 已保留为现行入口。脚本中的 build/m*、docs/evidence/m* 输出路径沿用历史约定，部分脚本会覆盖已有同名证据，运行前保存重要旧产物；没有统一添加新的覆盖策略。仅支持 --help 的入口可安全用于发现，其他顶层脚本导入即会执行验证。

| 脚本（原 tools/ 下同名文件） | 来源 |
| --- | --- |
| [macos_m0_2.py](macos_m0_2.py) | m0_2 阶段复现 |
| [verify_m0_3a.py](verify_m0_3a.py) | m0_3a 阶段复现 |
| [verify_m0_3b.py](verify_m0_3b.py) | m0_3b 阶段复现 |
| [verify_m1_1.py](verify_m1_1.py) | m1_1 阶段复现 |
| [verify_m1_2.py](verify_m1_2.py) | m1_2 阶段复现 |
| [verify_m1_3.py](verify_m1_3.py) | m1_3 阶段复现 |
| [verify_m1_4.py](verify_m1_4.py) | m1_4 阶段复现 |
| [verify_m2_1.py](verify_m2_1.py) | m2_1 阶段复现 |
| [verify_m2_2.py](verify_m2_2.py) | m2_2 阶段复现 |
| [verify_m2_3.py](verify_m2_3.py) | m2_3 阶段复现 |
| [verify_m3_1.py](verify_m3_1.py) | m3_1 阶段复现 |
| [verify_m3_2a.py](verify_m3_2a.py) | m3_2a 阶段复现 |
| [verify_m3_2b.py](verify_m3_2b.py) | m3_2b 阶段复现 |
| [verify_m3_3.py](verify_m3_3.py) | m3_3 阶段复现 |
| [verify_m3_4.py](verify_m3_4.py) | m3_4 阶段复现 |

共享 `mqsim_source_snapshot` 与 `sha` 已原样提取到 [source_snapshot.py](../../source_snapshot.py)，现行工具不依赖归档模块。归档脚本的断言、默认构建/证据位置不变；移位后的路径与输入哈希清单已同步。

文档中的调用路径已更新以便复跑，旧 manifest 内原命令和哈希未改写。归档脚本全量运行与当前完整 AC 是不同验证范围，不能互相冒充。
