# T4 内容与设备 profile

T4 保留 `independent-sector-v1` 布局、搜索/控制成本、FP32 输入和固定维序 FP64 L2。文件内容与模拟驻留独立；mmap 页命中没有模拟缓存优惠。实现/验证证据见 [T4 收口](t4-closeout.md)。

实验配置新增可选字段：

```json
{
  "content_backend": "mmap",
  "device_profile": "ann-2g-v1",
  "resource_budget_bytes": 4294967296,
  "simulation_timeout_seconds": 600
}
```

`mmap` 为默认；`memory` 保留原逐对象 ContentStore，供小数据交叉核对。两者均通过 Handles/Runtime 门控，算法没有文件描述符或映射 span。普通读须等全部 extents、DMA/CQ 完成；本地内容仅在 DeviceBuffer 可用；算术及结果传输完成后评分可见。只在受信读取时复制请求范围，邻接去掉固定槽 padding，公式布局与文件后端不保存 N 项对象/Placement map。query 文件仍一次读入，活动 query 各自复制。legacy v1 仿真输入改为分块验证；旧生成器/离线小 oracle 的全量对象路径保留。

映射采用 POSIX 只读 `MAP_PRIVATE`，对象生命周期长于全部引用。base/graph 启动及结束校验文件 SHA256、inode、大小与纳秒级 mtime/ctime；运行器复核 v1/v2 输入身份。实验期间文件必须不可变；这不是支持并发写入的快照协议，运行中截断可能触发操作系统错误。查询/元数据的开始结束身份由运行器校验。SHA256 使用固定状态计算，不复制整个输入。file-backed RSS 可能随访问页数增长，不应计为模拟主机驻留或私有内容副本。

| profile | 原始 NAND | 实测 namespace sectors（512 B） | 页 sectors |
| --- | --- | --- | --- |
| native-small | 64 MiB | 121897 | 8 |
| ann-2g-v1 | 2 GiB | 3900701 | 8 |
| ann-16g-v1 | 16 GiB | 31205621 | 8 |

`configs/profiles/devices.json` 为受控覆盖描述，以原有 `configs/native/ssdconfig.xml` 为共同基线，2×1×2×1、64 pages/block、4096 B/page，blocks/plane 分别 64/2048/16384。`tools/device_profiles.py` 同时生成 C++ 内嵌 XML/几何与运行器快照；CMake 依赖两文件及生成器，修改后必须重配/重编译。native-small XML 字节不变。不接受运行时任意 XML；XML SHA256 由实际元素返回并与配置快照比较，防止旧构建冒充当前配置。

启动前 checked arithmetic 检查布局末地址、整页向上对齐预装载边界、原始容量、query/消息大小及盘控前进块；构建 MQSim 后以其实际 namespace 再检查，不能用 raw×0.93 冒充 namespace。`preflight.json`、manifest 与 `device_profile.csv` 保存静态/实际值。静态资源预检采用 `pages*256 + blocks*4096` 字节的元数据估算并与显式 budget 比较，在创建 MQSim 前拒绝；**这是保守代理估算，不是经证明的 RSS 上界**，不包含历史事件、搜索、memory 后端内容或系统页缓存。完整阶段的预算监控仍须由 T5～T7 实现/验证，不以此宣称不会 OOM。

控制器从同一个 `Controller::bounds` 接口验证并计算 query 字节、确定性 chunk、最大块和接纳限制；单向量 sector_bound 不得小于实际独立对齐 stride。d128/d96、chunk16、scratch0、capacity16384、max_inflight4 时 block=8512，可接纳4条。d260 必须把 vector_sector_bound 设为1536；相同配置 chunk=9、block=14032、接纳2条。降额保留可前进空间，并非四块可并行保证。

全部 profile 保留逐页全布局写入、内部事件排空后建立 origin，不重置 MQSim 时间、FTL 或 CMT。容量构建探针和小布局运行不等于完整 1M/10M 预装载或查询矩阵通过。profile 改变会改变 FTL/CMT 压力；均为 `configured_unvalidated`，不代表商品 SSD 校准。

复跑 T4（先按 T2 准备真实小数据，输出须为新目录）：

```sh
/opt/homebrew/Caskroom/miniconda/base/bin/python3.13 tools/verify_t4.py \
  --prepared results/v0.2-t2/real-final --build build/delivery \
  --output results/v0.2-t4-new
```

此入口包含三个 profile 的实际 namespace/越界/资源拒绝、文件内容错误与可见性、小 d128/d96/d260 两后端两放置的完整事件/结果/模拟统计等价、独立逐批及流量/credit 核对，以及真实 SIFT 10k/32 query 对照。跨查询完成顺序允许交错，核对器按查询保留内部顺序和因果关系。完整 V0.1 AC 与映射后端 ASan 是单独证据；不自动启动 T5 或规模实验。
