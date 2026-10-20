# 620b 隔离评测实例（gagent-eval）

用途：自我进化评测专用。镜像与线上 CityU 实例相同（`gagent-app:p11`，主干 `a6fb1111`），但数据、网络和运行目录全部独立，不对外发布任何端口。线上实例（`gagent-*`，端口 40003）不受影响。

## 与线上的隔离对照

| 项目 | 线上（`gagent`） | 评测（`gagent-eval`） |
|---|---|---|
| compose 项目 | `gagent` | `gagent-eval`（`~/gagent-eval/deploy/`） |
| 容器 | gateway / app / proxy / ops | app / proxy（无 gateway、无 ops） |
| 对外端口 | 127.0.0.1:40003 | 无；评测经 `docker exec` 进入 app |
| 网络与子网 | front 172.28.0.0/24，sandbox 172.29.0.0/24，dockerapi 172.30.0.0/24 | gagent-eval-front 172.36.0.0/24，gagent-eval-sandbox 172.37.0.0/24，gagent-eval-dockerapi 172.38.0.0/24 |
| 数据库卷 | `gagent-data` | `gagent-eval-data`（空卷，启动后自建） |
| 运行目录 | `~/gagent/runtime` | `~/gagent-eval/runtime` |
| 产物输出 | 无 | `~/gagent-eval/evo-out` 挂为 `/evo-out`（不在 `/app` 下，文件工具读不到） |
| bio runner | `gagent-bio-runner`，监视 `~/gagent/runtime/bio_tools` | `gagent-eval-bio-runner`，监视 `~/gagent-eval/runtime/bio_tools`，`--keep-days 7` |
| 共用（只读或共享调度） | 镜像、`/data1/gagent` 的 conda 环境与数据库（只读）、`~/gagent/refdata`（只读，目前为空）、Slurm `work` 分区 |

## 测试开关（`.env` 相对线上的改动）

- `SKILL_LEARNING_ENABLED=0`、`QUALITY_EVALUATION_ENABLED=0`：不学技能、不做后台质量评分。
- `AMEM_ENABLED=0`、`LIGHTRAG_GATEWAY_ENABLED=0`：不接记忆与检索服务。
- `EXTENSIONS_MCP_ENABLED=0`：关闭 MCP 扩展（线上为 1，含外部检索类服务）。
- 召回：驱动在每轮请求里置 `context.memory_enabled=false`。
- 联网检索：没有单独的总开关；场景文本要求“只用上传的文件”，判分会检查是否编造了外部结果。

由 `setup_env.py` 从线上 `.env` 派生，只改上述键，其余行逐字保留，权限 0600。

## 启动与检查

```sh
# 1) 派生 .env（在服务器上运行，不打印取值）
ssh phage-server 'python3 -' < evals/evolution/instance/setup_env.py

# 2) 校验并启动（只起 app 与 proxy）
ssh phage-server 'cd ~/gagent-eval/deploy && docker compose -p gagent-eval -f docker-compose.yml config -q && docker compose -p gagent-eval -f docker-compose.yml up -d'

# 3) bio runner（用户服务，需要 XDG_RUNTIME_DIR）
scp evals/evolution/instance/gagent-eval-bio-runner.service phage-server:.config/systemd/user/
ssh phage-server 'export XDG_RUNTIME_DIR=/run/user/$(id -u); systemctl --user daemon-reload && systemctl --user enable --now gagent-eval-bio-runner.service'

# 4) 核对：不发布端口；线上网关仍返回 200
ssh phage-server 'docker port gagent-eval-app-1; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:40003/'
```

## 已知残留风险（需要知道）

1. proxy 与线上一样挂载了 Docker socket，且 `DOCKER_PROXY_POLICY_ENFORCE=0`（与线上一致，报告模式）。评测实例的 app 因此仍能通过 proxy 调用 Docker API，理论上能触及宿主上的其他容器。评测场景不包含这类操作，但这不是硬隔离。
2. app 容器能经 `host-gateway` 访问宿主端口，线上网关的 40003 可以从容器内被访问（需要登录，注册已关闭）。
3. bio runner 与线上 runner 共用同一个 Slurm 账户（`wangyichun`，与同事共用）。只按作业名或作业号取消，不使用 `scancel -u`。
4. 服务器账户与同事共享。卷和目录只在本实例名下创建；清理前先征得同意。

## 拆除（仅在获得同意后）

```sh
ssh phage-server 'systemctl --user disable --now gagent-eval-bio-runner.service; cd ~/gagent-eval/deploy && docker compose -p gagent-eval down'
# 卷 gagent-eval-data 和 ~/gagent-eval 含评测数据，删除前需要单独确认
```
