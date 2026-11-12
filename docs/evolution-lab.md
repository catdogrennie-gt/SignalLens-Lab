# 多场景验证环境

这是原固定锁等待站之外的独立入口与实验目标。原 `lab.toggle_web`、旧容器和旧公网端口保持原样。新增内容当前以代码实现、直接负载验证和完整平台验收三个层次分别记录，未通过完整链路的场景不能标为已验收。

## 组成与边界

- `compose.evolution.yaml`：独立 `evolution_lab` 项目，三个业务 MySQL (`db-a/b/c`)、两个应用 (`svc-a/b`)，均有内存/CPU上限。数据库不映射宿主端口。
- `compose.replica.yaml`：可选增加 `db-d` 与 `svc-c`。`db-c` 提供持续有限速的版本写入，`svc-c` 从真正的 `db-d` 副本读取。四库三应用总内存硬限制2880MiB；每库连接上限40，应用池6，账号最多8连接。
- `lab.business_app`：持续产生真实、有限的业务查询和请求计时，公开 `/metrics` 与只读 `/database` 采样；有凭据的 `/control` 只接受固定模式，不接受任意 SQL/命令。
- `lab.scenario_engine`：一个进程 owner、一个期望开关。同一时间只运行一个场景，重复 ON 不增加连接；换场景先清理旧负载。应用负载租约最长15秒，失去续期自行恢复正常。
- `lab.scenario_site` 与 `lab/web-v2`：新演示页；调用完整平台固定 target 白名单的只读投影与临时提频接口，不创建调查或调用模型。
- 场景目录和预期机制只存在演示/验收端。应用和数据库在监控中使用中性资源名；控制状态不输出到采样端点。

## 本机重建

准备 Linux Docker、Compose 以及单独的 Zabbix。先核实可用内存与磁盘，禁止把已有监控存储或业务库当作目标。

```sh
python -m lab.scenario_bootstrap
docker compose -f compose.evolution.yaml up -d --build db-a db-b db-c
# 确认三个库均已完成初始化，workload.orders 各有4000行后再启动应用。
docker compose -f compose.evolution.yaml up -d --build svc-a svc-b
```

生成的 `.runtime/private` 包含凭据和初始化 SQL，禁止提交。容器只挂载各自需要的私密文件。`signallens-internal` 是既有 Zabbix 可达网络；换环境时须修改该网络与 HTTP 地址。应用端口18231/18232应只允许监控与受控演示服务访问，控制接口还有独立 bearer 凭据。

数据库采用官方 `MySQL by Zabbix agent 2` 模板，建议使用单独 Agent 2 进程/配置。新增采样不得修改其他演示/业务主机的模板宏或重启原 agent。应用 HTTP 采集 `/metrics`；数据库补充采集 `/database`。HTTP主项读取 JSON，数值和摘要依赖项保存历史、每5秒更新，标记 `semantics=gauge|rate|counter` 和 `sl.sample_period=5s`。保留实际计时和初始差分不可用状态；首轮计数没有基线不能补零。

本轮实验的采集配置还包含以下约束，迁移或重建时须重新核实，不能把“主机已创建”当成采集正常：

- 新库监控账号的连接上限为12，业务账号为8；每库全局连接上限仍为40。监控主项会并发建立连接，原先4的上限曾造成真实采集拒绝，不能因监控账号限流把缺失数据解释为业务恢复。
- 实验专用 Agent 2 在10060端口，由独立 `evolution-zabbix-agent2.service` 托管；服务的 `ExecStart` 指向该实验私密配置，运行用户、配置权限和重启策略按目标机设置。使用 `systemctl is-active evolution-zabbix-agent2`、端口监听及日志共同检查，不能只用一次后台启动证明可持续运行。保留原默认 Agent 服务。
- 容器重建可能改变网络地址。重新查询实际容器网络地址，并核对 Zabbix 主机 DSN/Agent命名会话指向同一实例；秘密宏只保存监控凭据，不在日志、截图或源码输出。特别是源库和副本不得接反。
- 为缩短演示等待，本实验仅将新增4库的官方 `Get status variables` 主项设为10秒，未修改共享官方模板或其他主机。依赖项应继承实际样本时刻；平台登记的采样周期也必须与真实10秒一致。生产环境需按采集成本重新评估，不能照搬此频率。
- 配置完成后至少核对所选官方项的 `state/error/lastclock`，连续观测多个实际递增时间戳。更新应用及数据库后，再采正常基线至少90秒。采集缺失时停止平台效果判定，先修复数据链路。

在完整平台选择同一服务的应用/数据库指标，登记连接关系。先正常运行至少90秒形成真实基线，再测试。一个联合对象最多32项，应优先选择请求耗时、数据库耗时、池、队列、扫描/返回行、临时表、事务、连接和官方相关项。

## 新演示入口

安装 `requirements-web.txt`，另在源码外配置 `SCENARIO_SITE_CONFIG`，内容包括 `owner_lock`、`public_origin`、`platform_public_url`、`platform.url/token_path`、固定 `services` 的 `url/token`。平台 target key 只允许预注册的 `svc-a/svc-b/svc-c`，浏览器不能提交地址或凭据。

```sh
python -m uvicorn lab.scenario_site:create_app --factory --host 127.0.0.1 --port 18410
```

新入口默认为 OFF，不重放上次注入。页面区分负载控制状态、平台实际检查时间和历史报告，不显示虚构百分比。暂时没有报告或数据不能当成恢复。

目录的选择只保存在当前浏览器，查看另一个场景通过固定target的只读GET获取数据，不修改公共故障、全局scenario或revision。只有明确点击开关才改变真实负载；其他访客正在运行的场景在共享状态中单独显示。页面每60秒自动更新状态、指标与报告节点，也可点击“刷新数据”只读刷新，不创建采集或AI任务；更新不重建主页面、目录、展开框和焦点按钮。选中文本正在阅读报告时，自动刷新会保留该报告文字，手动刷新才应用新报告。

场景说明分业务现象、告警对照、联合线索三段。默认说明标为待验证假设，不代表平台已经发现或实际告警必然缺失。验收者可在 `verification_path` 对单项填写 `business_symptom`、`zabbix_comparison`、`platform_clues` 安全文本，写入经过实际事件/报告核对的结论；这些展示信息不进入Zabbix和AI输入。

`verification_path` 可指向源码外的验收状态 JSON，例如 `{"query-efficiency":{"status":"direct_verified"}}`。`direct_verified` 只说明负载和清理有真实验证。目录中的“发现与恢复已验证 · 分析待完善”说明 Zabbix→平台自主发现→新报告→关闭恢复的实际工作流已经留证，不能当作根因或分析质量通过。每项的“平台实际发现与分析边界”同时说明报告做到了什么、尚有哪些错误或缺口。验收文件每次读状态时加载，单项状态更新不需要重启控制器。

脱敏记录使用 `workflow_verified_with_limitations`，并分别保存 `workflow_verified`、`diagnosis_verified` 和 `diagnosis_limitations`。为兼容运行中的控制器，目录内部仍使用原有 `platform_verified` 编码；其页面含义以以上明确文案为准，不新增或放宽诊断通过的含义。

控制器有独占锁。更新新A时先确认OFF，停止该控制器再起新版；不能为了蓝绿发布同时启动两个owner。完整分析平台B的API/worker发布与此独立，旧公开A在新版本验证期间继续服务。

## 六类场景的观察重点

| 场景 | 真实发生的行为 | 应联合核对的数据 | 关闭后的验证 |
|---|---|---|---|
| SQL效率退化 | 固定有界查询产生更多扫描 | 请求耗时、DB调用、扫描/返回行、SQL摘要 | 新请求与窗口差分回到基线 |
| 应用池紧张 | 专属应用池6个连接被持有 | 池容量/等待、错误/队列、DB全局连接余量 | 故障持有连接0，等待和错误新窗口消退 |
| 临时表代价 | 小会话临时表容量下执行有界分组 | 请求/DB耗时、磁盘临时表速率、摘要 | 新临时表速率及请求时间恢复 |
| 应用排队反证 | 应用处理延迟，不向数据库加重查询 | 请求排队/耗时、DB调用、扫描代价 | 队列排空，不能把它误归因为数据库 |
| 长读与历史版本 | 持有一致性读，每秒最多2个测试版本提交 | 事务年龄、历史列表、提交与查询影响 | 读事务关闭，历史积压由InnoDB自然清理 |
| 副本读陈旧 | 在副本测试行上持有锁，源库仍提交版本 | 两端版本距离/数据时间距离、复制线程与真实延迟 | 释放事务，副本追上；两端版本距离重新接近0 |

长读场景每次开启最多提交600个测试版本，之后保留读视图而停止新增版本，避免长期开关造成无限积压。副本源库二进制日志按1小时过期、8MiB分段；副本relay日志预算64MiB。源写入根据实际版本距离作背压，距离到600即暂停，追上后再写，避免持续ON造成无限积压。启动前和扩容前核对内存、磁盘，独立数据卷预算5GiB；每次运行保存实际占用，旧公开站异常则先停止新增工作负载。不要把“当前模板没有对应告警”说成Zabbix不能配置这类监控；检查实际触发器宏、持续时间和事件，并保留不相关告警。

## 副本扩展重建

仅对新建隔离 `db-c/db-d` 操作。`db-c` 在快照及坐标捕获前不能运行写应用；不对已有业务实例执行这些步骤。准备脚本只写私密文件，不连接数据库，也不会自动重建复制。

```sh
python -m lab.replica_bootstrap
docker compose -f compose.evolution.yaml -f compose.replica.yaml up -d db-c db-d
docker exec -i evolution-db-c sh -c 'MYSQL_PWD=$(cat /run/secrets/root) mysql -uroot' < .runtime/private/c-replication.sql
# 源库无业务写入时取得实际一致性数据；仅导入全新的隔离副本。
docker exec evolution-db-c sh -c 'MYSQL_PWD=$(cat /run/secrets/root) mysqldump -uroot --no-tablespaces --single-transaction --set-gtid-purged=OFF workload' > .runtime/private/c-workload.sql
docker exec -i evolution-db-d sh -c 'MYSQL_PWD=$(cat /run/secrets/root) mysql -uroot workload' < .runtime/private/c-workload.sql
docker exec evolution-db-c sh -c 'MYSQL_PWD=$(cat /run/secrets/root) mysql -uroot -e "SHOW BINARY LOG STATUS"'
# 将上一步真实文件名和Position替换到参数；不能照抄样例坐标。
python -m lab.replica_bootstrap --source-log-file ACTUAL_FILE --source-log-position ACTUAL_POSITION
docker exec -i evolution-db-d sh -c 'MYSQL_PWD=$(cat /run/secrets/root) mysql -uroot' < .runtime/private/d-replication.sql
docker compose -f compose.evolution.yaml -f compose.replica.yaml up -d --build svc-c
```

`db-c` 默认server-id应先通过 `SELECT @@server_id,@@log_bin` 核实，须与 `db-d` 的14不同且二进制日志启用。现场重建若该前提不成立，应修正独立源库配置后重新取得快照和坐标；不能标为已验证。使用异步复制和行级测试事务，不能宣称能保证生产一致性或提前预警时长。

第三应用在 `/metrics` 输出实际 `read_version_gap`、`read_freshness_age_seconds`；`/database` 输出 `replica_io_running`、`replica_sql_running`、`replica_lag_seconds`。这些字段来自真实两端读数与 `SHOW REPLICA STATUS`，无观测时为null，不用0冒充正常。监控项用中性的资源/指标名，不发送“副本场景已开启”或期待原因给模型。

## 清理与验证

关闭开关停止固定负载。应用恢复正常模式、归还故障持有的连接、回滚长读事务；已提交的测试版本写入保留在独立 `ledger`，不会用回写时间或伪造采样抹除历史。普通请求继续运行，不能以应用仍有正常连接误判未清理。

每项记录基线、实际负载、Zabbix样本和当时告警、平台自主产生的报告、停止后的新采样。直接负载清理与平台监控恢复分别验收。测试包括重复ON、快速OFF/ON、场景切换、租约过期、单owner和失败时不续租。

2026-11-12六项隔离真实负载的开始/关闭时间、原始测量节选和清理结果见 [直接负载验证记录](validation/direct-target-results.json)。其中保留了临时表首轮权限错误，以及副本持锁因20秒空闲超时提前结束的耐久缺口、修正及真实60秒重测，未把失败试验写成成功。这份记录只证明注入与清理，不作为平台自动发现或AI结论通过的凭据。

```sh
python -m pytest -q
```

代码覆盖上述六类有界负载。实现及直接负载验证不等于平台已正确发现或解释，最终状态以主项目场景验证清单和A目录的实际验收记录为准。

## 可重复的逐项验收CLI

`tools/validate-scenario.py` 只调用A的固定目标只读接口与同一个公共开关。先确认A/B已加载本轮新协议、Zabbix已正常连续采样至少90秒；运行时不要另开其他实验。下面是操作示例，不是已执行通过的记录：

```powershell
python tools/validate-scenario.py --a-origin http://127.0.0.1:18410 --scenario query-efficiency --max-wait-seconds 360 --recovery-wait-seconds 150 --output D:\LabRuntime\validation\query-01.private.json
```

- `--scenario` 取目录key，如 `query-efficiency`、`pool-pressure`、`temporary-work`、`application-queue`、`retained-view`、`stale-replica`。预检与读取其他目标均为GET，不通过OFF写请求切换目录。
- `--max-wait-seconds` 为ON后等待自主发现/报告的上限，范围1–3500秒；`--recovery-wait-seconds` 为OFF后等待直接清理和后续监控观测的时间，范围0–1800秒。轮询默认3秒仅用于显式验收CLI，浏览器页面仍为60秒。
- 输出必须是源码外的绝对私密路径，已有文件会拒绝覆盖，应为每次重测使用新文件名。运行事件与不同样本会原子保存；不保存模型报告全文、账号、口令或私密配置。公开前仍应检查所选资源/监控项名称。
- ON使用generation/revision及独立validation_id防竞争；finally只对本次匹配的控制意图发OFF，不覆盖其他访客的操作。显式验收还带服务端到期截止，CLI崩溃/失联也会自动停止该测试；普通网页开关不增加时长、次数、冷却限制。
- 发现另一访客变更或控制器重启时记 `interrupted`，不能把其他场景的数据当本次恢复。读取失败、无新样本、旧报告、缺少信号维度均保留未知/未完成。

机器输出分别记录：负载请求接受与实际ON、ON后的新鲜数据、新signals、不同于基线的新report、桥接声明的当前episode关联、实际app控制查询的清理、OFF后的新监控观测。报告必须明确关联本轮新signal的标识、episode及host/item维度；同一目标另一个问题的新报告不能冒充本次成功。`diagnosis_quality` 始终为 `pending_human_review`，不能把 `completed` 或CLI退出0当作诊断正确率。

恢复比较仅对本次signals明确标出的host/item维度进行，要求OFF后两个不同的新鲜观测回到ON前值附近（相对容差50%，零值用数值精度容差）。这是通用验收观察，不是生产健康阈值；无维度/基线/新数据时不宣称恢复。报告模式只是桥接提供的标记，模型实际调用与诊断质量仍需结合B的记录人工审查。

退出码：0表示所需工作流观察已记录，2表示被中断，3表示前提不满足、错误或未完成。逐项检查真实输出、修复暴露的采集/发现/解释/展示缺口，再以新文件重测；根因和价值说明审核通过后，才更新平台验收标签与演示说明。

2026-11-12 的 [SQL效率场景第三轮工作流记录](validation/query-efficiency-iteration3.json) 来自该CLI的真实运行：产生新数据和4个新信号，普通巡检生成的新报告明确关联其中2个本轮信号，关闭后直接负载清理与后续监控回落分别留证。报告提供候选联合解释，未确认具体SQL或根因，引用范围和峰值/末值表述等限制原样记录。

随后串行完成的 [应用连接池场景第一轮工作流记录](validation/pool-pressure-iteration1.json) 同样区分各阶段：应用池6个连接全部占用，取连接等待600ms、队列和请求错误上升，同时数据库总连接保持9/40；平台自主生成报告，OFF后实际释放与后续监控回落分别确认。SQL摘要包含真实保活语句，扫描行数下降不能写成SQL执行计划退化；无实际DB调用的失败请求不伪造数据库耗时。实际模型输入遗漏池容量，报告还误读了样本数和投影省略，因此不能据此确认容量不足或根因。

第三项 [临时表场景第一轮工作流记录](validation/temporary-work-iteration1.json) 观察到请求/DB延迟由约1–1.5ms升至44ms、磁盘临时表约4个/秒，错误比例始终为0。普通平台发现并生成报告，关闭与后续回落均留证；但报告只解释延迟和查询速率相关，未定位临时表机制。具体三段说明、实际告警规则与分析限制见 [前三项验证边界](validation/observed-analysis-boundaries.md)。原模型报告保留，不把验证说明补充的知识回写成模型已经得出的结论。

发布新A前的交互检查：在指标表展开、滚动到报告中部并聚焦一个目录按钮，等待60秒刷新；确认滚动位置、目录选择、展开状态和焦点保持。切目录与“刷新数据”的网络请求必须只有GET，明确操作开关才出现PUT。程序回归包含 `tests/local_view_contract.cjs`；最终浏览器验收须在新版本受控加载后进行，不能通过点击旧版全局目录破坏正在运行的验收。
