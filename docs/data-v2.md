# T1 数据导入与 schema

`tools/benchmark_data.py import spec.json --output prepared` 导入，`check prepared` 复用前检查。输出必须不存在；转换在同目录临时目录完成全部校验后原子改名，失败不留下成功目录。不下载数据、不生成图或真值、不改变 FP32 字节。

Spec 示例（路径相对 spec；sha256 必须填写真实完整源文件哈希）：

```json
{
  "metric": "squared_l2",
  "preprocessing": "none by this project",
  "files": {
    "base": {"path": "base.fbin", "format": "fbin", "count": 100, "dimensions": 96,
      "sha256": "完整源文件SHA256", "source": {"url": "来源", "usage_conditions": "使用条件"}},
    "query": {"path": "query.fbin", "format": "fbin", "count": 20, "dimensions": 96,
      "sha256": "完整源文件SHA256", "source": {"url": "来源", "usage_conditions": "使用条件"},
      "selection": {"kind": "ids", "ids": [3, 1]}}
  }
}
```

角色为 base/query，另可声明 learn/groundtruth；浮点支持 fvecs/fbin，整数 ID 支持 ivecs/ibin。所有输入均为 little endian，显式 shape 与完整文件一致；检查每行头、长度、有限值及整数 ID 范围。每次读取最大约 8 MiB（单个异常超宽行例外）；不展开整个 base。selection 默认为 `{"kind":"all"}`，或显式无重复 ids，或 `{"kind":"random","seed":42,"count":32}`。random 使用运行 Python 的 random.sample，输出二进制映射是实际顺序证据；正式评价使用已冻结显式列表。全部映射按 u32 little endian 原始行号保存；选行后的局部 ID 为输出行号，T2 必须显式处理图/真值映射。

`dataset.json` 版本 `ndpsim-dataset-v2`，记录度量、预处理声明、转换器/校验器/spec 哈希；各角色记录 shape、dtype、endian、原始完整身份和来源、选择规则、转换后字节数/哈希及映射哈希。base/query/learn 输出无头 FP32；groundtruth 输出无头 int32，仅作为原始 ID 数据，不冒充已经绑定的 `ndpsim-truth-v2`。完整 base 的官方真值必须按 query 相同原始行号选行；base 子集附带官方真值会被拒绝，需 T2 独立重算。来源及预处理为用户声明，不自动认定官方来源；正式输入应取自 M0 内容锁。

`check` 不需要原始文件，重新校验输出字节、映射、shape、角色、数值与关系；不提供防恶意篡改的签名。输入实验期间不可变。v1 合成生成器、truth 和运行路径保持兼容；v2 准备产物在 run_graph 中校验后明确拒绝模拟，等待 T2 图/真值与 T3 接线。索引、真值、experiment schema 在相应任务实现，wire schema 不变。

验证记录见 [T1 收口](t1-closeout.md)。T1 不代表 M1 整体闭环完成。
