# MQSim 源码维护约定

MQSim 适配与修复直接在 `third_party/MQSim` 子模块中开发，平台 CMake 直接编译该目录；SST 则默认由根 CMake 在仓库 `.cache/sst/<配置键>/` 中准备、构建并私有安装，详见 [README](../README.md)。此规则也写入根目录 [AGENTS.md](../AGENTS.md)。不维护构建时应用的 patch 栈、不导出另一份集成源码再打补丁；提交源码即是后续开发和审阅的依据。

提交变更时，先在 MQSim fork 保存源码 commit，再更新主仓 gitlink。未提交的开发状态如实记录 HEAD、相对原生基线的 diff 哈希和实际文件哈希；这些哈希仅用于复现与核对，不用于构建时修改源码。不能只提交主仓而遗漏子模块中的实现，也不自动提交、推送、升级或初始化 Git。SST Core 固定且保持不修改，保留依赖的许可证。

## 原生 oracle

[deps.lock.json](../deps.lock.json) 保留 M0.2 原生基线 `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`、空补丁及原生工具链身份，不用它冒充已修改 MQSim 工作区的完整身份。

[原生构建工具](../tools/macos_m0_2.py) 始终以 `git archive <锁定原生 commit>` 导出独立源码来构建 oracle，不复制 MQSim 开发工作区，不应用适配改动。MQSim 工作区允许有修改，脚本记录其身份但不会将其用于原生构建；SST 工作区仍须干净。已有 oracle 源码和二进制保持不变，原生构建回执用于核对。

[M1.1 验证工具](../tools/verify_m1_1.py) 则直接构建 `third_party/MQSim`，记录 HEAD、相对原生基线的 diff 哈希及逐文件哈希，并断言构建/测试期间源码不变。它同时核对原生归档和旧回执、二进制身份，再运行原生小读对照。两个来源各自承担职责，不混用基线和开发源码的身份。

## M1.1 组织迁移记录

2026-10-27 按用户要求，将原有 M1.1 改动原样迁入子模块，删除 `patches/mqsim` 中的 patch/series/说明及 `tools/prepare_mqsim.py`，CMake 不再导出和修改构建副本。迁移只改变组织方式，MQSim 改动内容保持不变：

- [Engine.h](../third_party/MQSim/src/sim/Engine.h)、[Engine.cpp](../third_party/MQSim/src/sim/Engine.cpp)：抽出初始化阶段，不新增事件步进。
- [Sim_Object.h](../third_party/MQSim/src/sim/Sim_Object.h)、[NVM_Channel_Base.h](../third_party/MQSim/src/ssd/NVM_Channel_Base.h)、[IO_Flow_Base.h](../third_party/MQSim/src/host/IO_Flow_Base.h)：虚析构。
- [Sim_Defs.h](../third_party/MQSim/src/sim/Sim_Defs.h)：库错误抛出异常，不读取 stdin。
- [ONFI_Channel_Base.h](../third_party/MQSim/src/ssd/ONFI_Channel_Base.h)、[TSU_Priority_OutOfOrder.cpp](../third_party/MQSim/src/ssd/TSU_Priority_OutOfOrder.cpp)：释放原有数组。
- [IO_Flow_Synthetic.h](../third_party/MQSim/src/host/IO_Flow_Synthetic.h)：可选随机数生成器指针初始化为空。

[迁移核对](evidence/m1.1/direct-source-migration.json) 记录旧补丁哈希、旧构建副本回执、九个改动文件的 SHA256；全部 175 个受 Git 管理的 MQSim 文件与旧构建副本逐字节一致。旧验收记录作为历史保存，新组织方式的验证另存 [direct-source 验收](evidence/m1.1/direct-source/manifest.json)。直接源码干净构建复验共 38 条断言通过，包含 CTest 4/4、四种 ASan 探针、元素加载/生命周期、打开 stdin 的非法配置退出，以及原生归档身份和重复小读回归。本轮没有创建或推送 MQSim commit、更新主仓 gitlink，也没有改变 M1.1 功能范围或启动下一任务。


## M4.3 当前交付身份与完整适配清单

2026-10-28 本轮开工核对：MQSim HEAD 和主仓 gitlink 均为 `8f7ddf6d8cafdb7c783b3fcfd4693f15c6c5854b`，已保存适配，无工作区修改；SST HEAD/gitlink 均为 `ffd71527bc26a1f2a4fb685fdeb2ca0799997d78`，无修改。上面的 M1.1 九文件清单只是历史迁移范围；当前相对锁定原生基线共 24 个文件：

| 目的 | 相对原生 commit 改动的文件（均在 third_party/MQSim/src/） | 默认行为/基线影响与验证 |
| --- | --- | --- |
| 初始化、时间片推进、时间/同刻保护 | sim/Engine.h、sim/Engine.cpp | 保留原生 Start_simulation；平台显式调用 initialize/peek/synchronize/dispatch；AC-03/04/08 |
| 生命周期、错误诊断、数组回收 | sim/Sim_Object.h、sim/Sim_Defs.h、host/IO_Flow_Base.h、host/IO_Flow_Synthetic.h、ssd/NVM_Channel_Base.h、ssd/ONFI_Channel_Base.h、ssd/TSU_Priority_OutOfOrder.cpp | 虚析构、指针初始化、库错误抛异常而不等待 stdin；不等于修复全部上游生命周期/RNG 问题；CTest、ASan 独立探针及原生回归 |
| 映射/CMT/初态读取与缓存能力检查 | ssd/Address_Mapping_Unit_Page_Level.h、ssd/Data_Cache_Manager_Base.h、ssd/Data_Cache_Manager_Flash_Simple.h | 支持确定性预装载及研究观测；仅 SIMPLE/TURNED_OFF 本地读；AC-08 初态与小 CMT 对照 |
| 设备本地只读与请求/事务关联 | ssd/Host_Interface_Base.h、ssd/Host_Interface_NVMe.h、ssd/Host_Interface_NVMe.cpp、ssd/NVM_Transaction.h、ssd/User_Request.h | 显式 DEVICE_LOCAL route；复用拆分/FTL/NAND，在信号扇出结束后同刻清理；不生成 NVMe 完整数据返回/SQ/CQ；普通 route 仍走原路径；AC-06/08 |
| 共享 PCIe 与平台 envelope | host/PCIe_Link.h、host/PCIe_Link.cpp、host/PCIe_Message.h、host/PCIe_Root_Complex.h | 平台显式发消息，普通读共用双向 FIFO；保留原生计费/截断，增加参数检查和重入保护；AC-06/08/11、混发探针 |
| 请求/channel/die 观测 | sim/Observation.h、nvm_chip/flash_memory/Flash_Chip.h、nvm_chip/flash_memory/Flash_Chip.cpp | 显式 observer，未订阅时无平台统计；回调不新增模拟延迟；AC-09 独立区间并集与日志开关对照 |

准确差异以 `git -C third_party/MQSim diff --binary 51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16 --` 为准；当前列表不替代 diff。统一 [交付入口](../tools/run.py) 在构建/验收前后比较实际源码快照，manifest 保存 native_base_commit、head_commit、diff_from_native_base_sha256 和 files_sha256。原生 commit 和空 patches 字段在 deps.lock.json 中仅表示未修改 oracle，不表示开发 fork 无差异。

维护时先说明改动目的/开关/普通路径影响，在子模块原处编辑并测试；涉及存储、时间、初态或链路时必须独立导出原生 oracle 运行 AC-08，不能只比较两份共享适配代码。新功能还须验证其 AC、资源守恒、错误清理并更新本表与限制。保存 fork 提交并使其可取得后才更新主仓 gitlink；原生 oracle commit 不随 fork 适配提交自动移动，SST 不改动。升级上游是单独工作，须重新核查许可证、差异、回归和模型结论，不能承诺无冲突升级。本轮没有执行任何提交、推送、初始化或升级。
