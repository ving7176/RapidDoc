# Changelog

## 2026-08-20

### fix: 合入大 Excel OOM 修复（来自 fix/large-excel-oom 分支 dbfcb69）

- 新增大 Excel 轻量级解析路径：sheet XML >5MB 时跳过 openpyxl，用 iterparse 流式解析（`_LightweightSheet`/`_LightCell` 仅存非空单元格），内存峰值 12GB+（OOM）-> 约 2.2GB
- 连续 10 行空行截断、合并区域按数据边界过滤（18.6 万 -> 115 个）、多 sheet 独立解析 + 逐 sheet 释放内存、从 ZIP 直接提取大 sheet 图片
- `_find_true_data_bounds`：合并区域不再参与整体边界计算，避免异常文件洪水填充遍历百万级空单元格导致 OOM
- `gap_tolerance` 参数全链路透传：`convert_binary` / `office_analyze` / CLI（`do_parse`/`aio_do_parse` kwargs）/ Docker API（`/file_parse` Form 参数）

### fix: 修复合入代码的两处行为退化（保留 RapidDoc 原有能力）

- `xlsx_converter.py` `_convert_package_stream`：小文件 openpyxl 分支恢复「多 sheet 标题插入」逻辑（`sheet_pages` + `_should_emit_sheet_titles` + `_prepend_sheet_titles`），仅新增大 sheet 分流，不丢失 commit 4f8edac 的多工作表标题功能
- `xlsx_converter.py` `_select_best_gap_candidate`：恢复 near_best + tie-break 选优（severe_separator_count / gap 偏好 / 空行比排序），同时保留每轮 `del + gc.collect()` 串行 GC 降内存峰值

### feat: images_manifest 对账契约、解析超时 504、原图确定性回注（4a9c8b5）

- `docker/app.py`：`/file_parse` zip 模式返回包新增 `images_manifest.json`（file/sha256/bytes/width/height/page_idx/bbox，页码与位置取自内部强制落盘的 middle_json，兼容 paras/preproc_blocks 两种结构）；`asyncio.wait_for` 超时防护（`RAPIDDOC_PARSE_TIMEOUT`，默认 600s），超时返回 504 并清理输出目录；`gap_tolerance` API 参数
- `pipeline_analyze.py`：xref 原图无条件提取（扫描/截图页不再置空）；页级进度心跳（每 10 页）
- `utils.py`：`reinject_uncovered_ori_images` 确定性回注（未被布局图片/表格覆盖的原图以合成块回注，`RAPIDDOC_REINJECT_ORI_IMAGE` 可关）
- `batch_analyze.py`：布局检测后无条件调用回注

### 验证

- `py_compile` 全部改动文件通过
- demo/xlsx 8 个样例回归：全部成功（含 chartsheet / inflated / edge_cases / gap_tolerance / one_cell_anchor）
- 多 sheet 小文件：标题恢复输出、表格正常、openpyxl 路径不变
- 大文件（6 万行 x 10 列、60 万单元格、33MB sheet XML）：正确触发轻量级路径、输出完整、峰值 RSS 约 2.5GB
- `office_analyze(gap_tolerance=1)` 透传链路正常
