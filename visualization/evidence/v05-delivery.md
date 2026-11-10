# V05 独立安装与 B/C 复现

沿用 [V01 样本登记](v01-samples.json)。教学样本只使用 capture，单次运行使用 B01，正式比较使用 B01/C01 同包及原始 query 2579。输入按散列复制到独立目录，复制件设为只读。CLI 在该目录运行，清除 `PYTHONPATH` 等环境覆盖，PATH 只含虚拟环境和系统目录，确认没有 SST、MQSim、仿真 CLI 或仿真 Python 模块。Perfetto 仅作为独立核查工具。

从仓库根目录运行。构建机 Python 需已有 setuptools 77 或更新版本。下例构建和安装均禁用网络，`trace_processor` 由调用方提供。

```sh
out=$(mktemp -d)
mkdir "$out/source"
cp visualization/README.md visualization/pyproject.toml "$out/source/"
cp -R visualization/src "$out/source/"
python3.13 -m pip wheel --no-deps --no-build-isolation --no-index \
  --wheel-dir "$out" "$out/source"
python3.13 -m venv "$out/venv"
"$out/venv/bin/python" -m pip install --no-index --no-deps "$out"/*.whl
python3.13 visualization/evidence/check_v05.py "$out" /absolute/path/to/trace_processor
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=visualization/src \
  python3.13 -m unittest discover -s visualization/tests -v
git diff --check
```

核查脚本记录三次 CLI 运行的参数、退出码、墙钟、峰值 RSS、索引字节数和产物散列。墙钟只计已安装 CLI 从启动到退出，含新索引构建和导出，不含安装、复制或 Perfetto。峰值 RSS 来自 CLI 子进程，macOS 上以字节计。正式结果从输出 DAG 读取稳定键，使用 Perfetto 精确查询两个切片，再独立与源 CSV 和 admission 比较。

## 实测结果

macOS 26.5.1 arm64、Python 3.13.12、ndpsim-viz 0.4.5、Perfetto v59.1。离线 wheel 构建和安装、三次 CLI 导出、Perfetto 查询及 `git diff --check` 均退出 0。Python 单测 16/16。安装的 wheel SHA-256 为 `de6bdb4fb99783730b4f4af6ccf577346cdf930a6e7cc3a9ac940b98e17f7012`。产物大小和 SHA-256、各模式测量及精确查询结果见 [机器记录](v05-delivery.json)。manifest 记录了本地输入路径，换目录后其散列会变化。

| 模式 | 墙钟秒 | 峰值 RSS（字节） | 三个输出文件（字节） | SQLite（字节） |
| --- | ---: | ---: | ---: | ---: |
| Capture-only | 0.643 | 41,517,056 | 1,925,166 | 28,672 |
| B01 | 19.777 | 65,617,920 | 10,085,422 | 49,053,696 |
| B01/C01 | 25.102 | 67,911,680 | 12,767,757 | 56,250,368 |

Perfetto 按 DAG 中 `{"query":"2579","op":"op2","stage":"score_l2.1"}` 查询得到 Host 切片 76（起点 154,209 ns，时长 99 ns）及 Controller 切片 58（起点 77,321 ns，时长 77,018 ns）。两侧均与原始 CSV 的阶段端点和各自 admission 一致。阶段区间包含必要输入与返回。
