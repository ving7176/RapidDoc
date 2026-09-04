# RapidDoc 项目规范

## 定位

RapidAI/RapidDoc 的 fork：PDF / Word / PPT / Excel 转 Markdown 的文档解析引擎（ONNX Pipeline，无 VLM）。fork 上叠加大 Excel OOM 修复、图片兜底、images_manifest 契约等本地补丁，历史见 CHANGELOG.md。

## 技术栈与常用命令

- Python >=3.10（<3.15），setuptools 构建，依赖集中在 pyproject.toml
- 安装：`pip install -e ".[test]"`
- 测试：`pytest tests/`（tests/ 混有部分手工验证脚本，非全是 pytest 用例）
- 服务：`docker/app.py`（FastAPI，`/file_parse` 接口）；服务器部署用 `docker/deploy.sh`
- 快速验证单文件解析：根目录 `demo.py`

## Git 约定（重要）

| remote | 地址 | 用途 |
|--------|------|------|
| origin | git@gitee.com:kkje/rapid-doc.git | 主源，日常 pull/push |
| github | git@github.com:ving7176/RapidDoc.git | fork 镜像 |
| upstream | https://github.com/RapidAI/RapidDoc.git | 仅保留关联 |

- `git push` 已配置同时推 Gitee + GitHub（origin 双 pushurl）
- **禁止 `git fetch --all` / fetch upstream**：国内网络下访问 GitHub 会超时挂起；只 `git fetch origin`
- 服务器拉取一律走 Gitee
- 不主动 commit/push；改动完成后先更新 CHANGELOG.md 再由用户确认提交

## 目录结构

- `rapid_doc/` 核心包：`model/`（layout/formula/table/ocr/xlsx）、`backend/`、`cli/`、`utils/`
- `docker/` FastAPI 服务与部署：`app.py`、Dockerfile、`deploy.sh`；`app.py` 内 `patch_version` 标记本地补丁版本
- `chunker/` 文本分块子模块
- `demo/` `demo.py` 示例输入样例
- `tests/` 测试与实验脚本

## 开发规则

- Dockerfile 层序有意把模型下载层前置以保证缓存命中，调整时勿破坏该设计
- 大 sheet（XML > 5MB）走 iterparse 流式轻量路径，勿改回 openpyxl 全量加载
- 改动后最低验证：`py_compile` 全部改动文件 + 相关 tests/demo 回归（xlsx 样例回归必跑）
- 进度唯一记录在 CHANGELOG.md，按日期分节
