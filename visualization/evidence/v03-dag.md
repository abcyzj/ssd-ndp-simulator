# V03 离线 DAG 验证

输入沿用 [V01 样本登记](v01-samples.json)。教学 query 0 以 capture-only 模式生成，正式 query 2579 使用 P19 B01/C01 两份运行报告。前端用 Cytoscape.js 3.33.1 绘制，使用 elkjs 0.10.0 的 layered 算法计算位置。esbuild 0.25.12 生成随工具分发的脚本与模板，打开页面只读取自身。布局默认按算子折叠，展开后显示实际阶段边。合并到同一可见边的原始依赖仍逐条保留方向、类型和端口。

从仓库根目录执行。Chrome 使用独立配置目录，浏览器脚本通过本机调试端口检查页面，主动阻止 HTTP 和 HTTPS 请求。

```sh
(cd visualization/web && npm ci && npm run build)
export PYTHONPATH=visualization/src PYTHONDONTWRITEBYTECODE=1
python3.13 -m unittest discover -s visualization/tests -v
node --test visualization/web/graph.test.js
out=$(mktemp -d)
python3.13 visualization/evidence/render_v03.py "$out"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --no-first-run --disable-background-networking --disable-gpu \
  --user-data-dir="$out/chrome" --remote-debugging-port=0 about:blank &
chrome_pid=$!
until test -f "$out/chrome/DevToolsActivePort"; do sleep 1; done
node visualization/evidence/check_v03_browser.mjs "$out"
kill "$chrome_pid"
shasum -a 256 visualization/web/package-lock.json visualization/src/ndpsim_viz/assets/* "$out"/*.html
git diff --check
```

最终记录为 Python 3.13 单测 10/10，Node 26.13.0 单测 2/2，Chrome 157.0.8103.39 浏览器检查退出 0，`git diff --check` 退出 0。使用本轮已下载依赖重复运行 `npm ci --offline` 及 `npm run build` 均退出 0。浏览器仅发出两个本地页面读取请求，JavaScript 异常为零。

- 教学默认 3 个节点和 2 条边，检查两条入边可见、原始 DATA 边指向 `join/sum`，搜索与折叠正常，展开后阶段键精确，时序显示未提供。
- 正式样本搜索 `graph.SCORE_L2` 并展开 `op2`，逐个选中 `score_prepare`、`score_l2`、`merge_append`，B01/C01 各阶段均显示完成。浏览器验证了可复制字段的精确内容，系统剪贴板权限依浏览器设置。
- 纯前端用例覆盖多条不同类型原始依赖合并及展开后恢复；Python 用例覆盖 uint64 原始 ID、显式依赖端口以及嵌入数据的 HTML 边界。

| 文件 | 字节（生成页面） | SHA-256 |
| --- | ---: | --- |
| `web/package-lock.json` | | `1c80a94d7b94c3468ac437e06098fe46ad7984dbc979522294c0683c2f9a2901` |
| `assets/dag.html` | | `be189f55219b5770322773ab479565e122b0aa23b76c48ad0e165433e0e22ed3` |
| `assets/dag.js` | | `21e72394bb3b57b1e277f10b1b5d7bdde4da7a0ed93ccd5f1ae828423df302f2` |
| `assets/THIRD-PARTY-LICENSES.txt` | | `28a11161a937ed9ff910a90020bfa565a8370e180be207236745634e761752d1` |
| `teaching.html` | 1,923,327 | `7b9cbc2736772a222dd9f236f47a392f95dfc0eba9584a30657fe547f57b362e` |
| `formal.html` | 2,712,025 | `76308534c1ba6a6d53135e9193d768f5858b309c9c4b3b4cfa6f9d4be957f596` |
