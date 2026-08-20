# RapidDoc 镜像部署指南

镜像已推送至 [Docker Hub](https://hub.docker.com/r/hzkitty/rapid-doc)

## 镜像构建

如果需要自己构建镜像

### 执行构建命令

```bash
cd docker

# 1. CPU 模式
docker build -f Dockerfile -t hzkitty/rapid-doc:0.9.9 .

# 2. GPU 模式
docker build -f DockerfileGPU -t hzkitty/rapid-doc:0.9.9-gpu .
```


## 运行部署

### 1. CPU 模式

仅CPU推理，资源占用较少：
```bash
docker-compose -f docker-compose.yml up -d
```
### 2. GPU 模式
```bash
docker-compose -f docker-compose-gpu.yml up -d
```

## 服务端口

- **8888**: RapidDoc Web API 服务端口

## API 使用

### 健康检查

```bash
curl http://localhost:8888/health
```

### 文档解析 API

```bash
# 上传文档进行解析
curl -X POST "http://localhost:8888/parse" \
     -F "file=@document.pdf" \
     -F "mode=pipeline"
```

## 配置文件详解

### .env 环境变量配置文件

`.env` 文件用于配置服务器和系统运行参数，支持以下配置项：

#### 基础配置

| 变量名                                | 默认值      | 说明                   |
|------------------------------------|----------|----------------------|
| `API_PORT`                         | `8888`   | RapidDoc Web API 端口  |
| `PADDLEOCRVL_VERSION`              |          | paddleocr-vl 版本      |
| `PADDLEOCRVL_VL_REC_BACKEND`       |          | paddleocr-vl backend |
| `PADDLEOCRVL_VL_VL_REC_SERVER_URL` |          | paddleocr-vl url     |


### 系统配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `STARTUP_WAIT_TIME` | `15` | 启动等待时间（秒） |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `RAPID_MODELS_DIR` | `/app/models` | 模型文件存储目录 |
| `RAPIDDOC_PARSE_TIMEOUT` | `600` | 单文件解析超时（秒），超时返回 504 |
| `RAPIDDOC_REINJECT_ORI_IMAGE` | `true` | 是否回注未被覆盖的原图（`false/0/no` 关闭） |

## 一键部署（固定 A 方案，推荐长期使用）

仓库以 **Gitee 为主源**（日常开发 + 服务器拉取，国内快且稳），GitHub fork 仅作同步镜像，官方 RapidAI 为 upstream。

### 一次性初始化（服务器上，仅需执行一次）

把部署脚本下载到固定位置，后续永远复用：

```bash
mkdir -p /opt/rapiddoc
curl -fsSL https://gitee.com/kkje/rapid-doc/raw/build/docker-optimization/docker/migrate_and_deploy.sh -o /opt/rapiddoc/deploy.sh
chmod +x /opt/rapiddoc/deploy.sh
```

### 之后每次重新部署（唯一需要记住的命令）

```bash
/opt/rapiddoc/deploy.sh
```

脚本每次自动完成（幂等，反复跑安全）：

1. 定位 `/opt/rapiddoc`（无则从 Gitee 克隆 `build/docker-optimization` 分支）
2. **强制把 origin 切到 Gitee**（无论服务器之前是 GitHub / hzkitty 源）→ `git fetch` + `git pull` 拉到最新代码
3. 停旧容器（`docker compose down`）
4. `docker compose build`（重新构建镜像，模型层命中缓存不重复下载）
5. `docker compose up -d` 启动
6. 健康检查（等待服务就绪）

> 你本机 `git push`（双推 Gitee + GitHub）后，服务器只需重跑这一条命令即可部署到最新代码。
> 可选参数：`APP_DIR`（默认 /opt/rapiddoc）、`REPO_URL`（默认 Gitee）、`BRANCH`（默认 build/docker-optimization）、`API_PORT`（默认 8888）。

### 备选：连脚本本身也每次拉最新版

脚本改动频率低，一般用上方固定版即可；若需脚本也自动更新，可用：

```bash
bash <(curl -fsSL https://gitee.com/kkje/rapid-doc/raw/build/docker-optimization/docker/migrate_and_deploy.sh)
```

## 模型缓存机制（重要）

### 层结构与缓存命中

`Dockerfile` 把**模型下载层前置**，业务代码层放最后：

```
COPY download_file.py download_models.py models_download_utils.py /app/
COPY rapid_doc/model/ /app/rapid_doc/model/
RUN python3 download_models.py      <- 模型层（前置，依赖面最小）
COPY rapid_doc/ /app/rapid_doc/     <- 代码层（最后）
```

因此：**只更新 office/xlsx/utils/pipeline/cli 等业务代码时**，`rapid_doc/model` 目录不变，模型层整层命中 Docker 缓存，**不会重复下载模型**，构建秒级完成。

`download_models.py` 底层自带 sha256 跳过逻辑（`download_file.py` 的 `_should_skip_download`）：目标文件已存在且校验匹配 → 跳过；不匹配 → 重新下载，保证下载内容正确。

### 首次改造后需重下一次模型（一次性成本）

调整层顺序会改变镜像结构，旧镜像的模型层与新增模型层无缓存关联，因此**首次构建需要重新下载全部模型**（几百 MB），属正常现象；之后更新代码不再重复。

### 是否需要先删除旧镜像？

- **构建过程不需要任何删除前置动作**。Docker 层缓存相互独立，旧镜像里的旧模型与新构建不冲突、不占用新镜像空间，`build` 会自动复用/覆盖tag。
- **旧镜像会残留磁盘空间**（旧模型层 + 旧代码层）。建议在新镜像构建并启动、健康检查通过后，按需回收：
  ```bash
  docker image prune            # 清理悬挂镜像（<none>，不动有tag的）
  # 或精确删除旧tag：
  docker rmi hzkitty/rapid-doc:0.9.9
  ```
  删除前确认新容器已正常运行，避免误删正在使用的镜像。