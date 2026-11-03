# V0.3 配置归档索引

V0.3 没有可独立迁移的专用废弃配置；它继承正式配置并由工具动态生成双引擎矩阵。这里只收拢版本说明，保留有效配置的原路径、字节和锁定身份。

| 原路径 | 继续保留的原因 |
| --- | --- |
| `configs/experiments/{host,controller}.json` | 日常默认 IR/SSD/原成本；显式 direct 仍可对照 |
| `configs/regression/ir.py` 及其他 regression 夹具 | CTest、IR因果/资源/生命周期及继承断言 |
| `configs/benchmarks/` | 原始输入、图、查询与预算锁；tuning32/evaluation128身份不能重排 |
| `configs/experiments/v0.2/sift1m.json` | 正式准备与固定矩阵模板；V0.3双引擎矩阵和V0.4继续继承 |
| `configs/native/`、`profiles/`、`sst/` | 原生oracle、设备和模拟接线的现行依赖 |

[V0.3 发布记录](../../../docs/releases/v0.3.md)保存完整矩阵证据；[V0.4 矩阵](../../../docs/validation-matrix-v0.4.md)定义新常规14配置，尚未实现新验收入口。V0.4 新 CPU Profile/目标内存驻留须显式启用，不能通过本次整理改变旧默认。
