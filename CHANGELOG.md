# Changelog

## 2026-09-04

### chore: 入库项目规范 AGENTS.md 与输出打包工具，gitignore 补本地产物

- 新增 `AGENTS.md`（fork 定位、remote 三源约定、目录结构、开发规则）与 `package_rapiddoc_output.py`（RapidDoc 输出目录打包为 MinerU 同构 ZIP）
- `.gitignore` 追加 `.zcode/`、`*.egg-info/`；`bench_tmp.py`（硬编码本机路径的临时压测脚本）保留本地不入库
- 线上验证：`http://10.1.14.96:8888` 已发布 `20260904-p7`，枫向标 PDF 冒烟 3 套餐 22/22 关键字命中，36.3s

### fix(p7): 位图表格被文本层提取挡住导致整表空白，新增覆盖率兜底回退 OCR

- 现象：数字版 PDF 内嵌位图表格（如「紧急枫向标20260821」p4 早安营养套餐表），版面检出 table 但框过大罩进框外标题文字，`_extract_table_text_from_pdf` 从文本层提取非空 → 跳过表格 OCR → 输出全空单元格
- 修复：`analyze_utils.py` 新增 `calc_table_pdf_text_coverage`（表格 det 文字框被 PDF 文本层覆盖的面积占比），`_extract_table_text_from_pdf` 提取后覆盖率低于 `pdf_text_coverage_threshold`（默认 0.7）则丢弃文本层结果回退表格 OCR；设 0 关闭（等价旧行为）
- 阈值实测：文字表格 coverage=1.000（财报 38 框），位图表格+框外标题污染 coverage=0.528（9 框），0.7 两侧余量充足
- 二次缺陷：`txt_spans_extract` 会原地 remove 无文本来源的低对比度 span（框架套位图表实测 28 框删到 6 框），覆盖率改在删除前对全量 det 框做快照计算，否则位图内容 coverage 虚高成 1.0 漏判
- 全文档验证：「紧急枫向标20260821」12 页 3 个套餐（早安营养蛋香/营养蛋香/营养均衡）29 个产品关键字全部命中，行列结构完整
- 单测：`tests/test_table_pdf_text_coverage.py`（13 例：覆盖率计算 + 回退分支 mock + 快照语义）
- 回归：比亚迪财报文字表格 coverage 1.0 不回退、数值完整；xlsx_07 样例通过
- `docker/app.py`：`patch_version` → `20260904-p7`

## 2026-08-27

### docs: 初始化 AGENTS.md

- 新增项目规范：remote 三源约定（Gitee 主源、禁 fetch upstream）、目录结构、常用命令、开发规则（Dockerfile 模型层缓存、大 sheet 流式路径保护）

## 2026-08-20

### feat(p6): 图片兜底与 manifest 契约优化（build/docker-optimization = 4002eed）

- 图片兜底第 1 层补全不再依赖 `ori_image_list`：用 image_body 合法 bbox 从页面渲染图裁剪落盘，对 pdfium 提取问题免疫
- 第 2 层 ori 追加默认关闭，由 `RAPIDDOC_ORI_APPEND`（默认 false）控制，避免表格/公式区域截图误补进 md
- `docker/app.py`：`patch_version` → `20260820-p6`；`build_images_manifest` 改为只收录 middle_json 中 md 引用的 IMAGE span 落盘图，用 md 引用反索引替代目录 glob，表格/公式截图不再进 manifest
- 环境变量：`RAPIDDOC_MIN_ORI_AREA_RATIO`（默认 0.08，第 1 层阈值）、`RAPIDDOC_ORI_APPEND`（默认 false）

### fix: 部署脚本卡在 Fetching upstream（国内网络访问 GitHub 超时）

- 根因：`git fetch --all --prune` 会同时 fetch 官方 upstream（GitHub），国内网络下超时挂起
- 修复：改为 `git fetch origin --prune`，只拉 Gitee 主源，不碰 upstream
- 服务器善后：删除已添加的 upstream remote 并重新下载脚本（见 README）


- README 部署章节改写为「固定 A 方案」：一次性初始化（下载脚本到 /opt/rapiddoc/deploy.sh）+ 每次重部署仅 `deploy.sh` 一条命令
- 脚本幂等，自动强切 origin 到 Gitee、拉代码、停旧容器、build、启动、健康检查
- 服务器统一用 Gitee 拉取，规避 GitHub TLS 断连；GitHub fork 仅作同步镜像


- remote 调整：`origin` → Gitee（`git@gitee.com:kkje/rapid-doc.git`，主源，日常开发 + 服务器拉取）；`github` → GitHub fork（镜像）；`upstream` → 官方 RapidAI/RapidDoc（保留关联）
- `git push` 配置为同时推 Gitee + GitHub（origin 两个 pushurl，`.git/config`）
- `migrate_and_deploy.sh` 默认源改为 Gitee，clone/pull 加 3 次重试规避国内网络瞬时断连
- 服务器端从此通过 `https://gitee.com/kkje/rapid-doc.git` 拉取，规避 GitHub TLS 断连


### build: 优化 Docker 模型下载缓存，避免代码更新时重复下载模型

- 调整 `docker/Dockerfile` 层顺序：模型下载层前置（只 COPY 下载脚本 + `rapid_doc/model/` 子结构），业务代码层放最后
- 效果：日后只更新 office/xlsx/utils/pipeline 等业务代码时，`rapid_doc/model` 目录不变 → 模型层整层命中 Docker 缓存，不再重新下载几百 MB 权重
- 校验：`download_models.py` 自带 `_should_skip_download`（文件存在且 sha256 匹配则跳过，不匹配则重下）；`rapid_doc/model/__init__.py` 为空、`office_stream.py` 仅依赖标准库，模型层 `import rapid_doc.model.*.configs` 不依赖 model 之外代码
- 新增 `docker/deploy.sh` 一键部署脚本（git 拉取 fork 分支、停旧容器、构建、启动、健康检查）


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
