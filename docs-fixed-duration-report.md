# 基于 MySQL 的 Spring 应用读写性能测试与瓶颈定位

〔作者姓名，组内成员，按学号顺序 —— 待填〕

**摘 要**：数据库访问往往是 MySQL 后端的 Spring 应用的主要瓶颈，而"读快写慢"是流传最广的经验结论。本实验在同一套四虚机环境、同一个 Spring Boot + JPA 应用上，用 JMeter 5.6.3 以非 GUI 模式对三类 RESTful 接口做阶梯加压测试——按主键读商品、创建商品（写）、按名称搜索商品——以"80% 的请求响应时间小于 40 ms"为判据，逐级逼近每种负载下能够有效处理请求的并发临界点。与常见做法不同，本实验以"固定时长 + Forever 循环"取代"线程数 × 循环数"来施加负载，并用结果文件中记录的同时存活线程数反向校验施压是否真的成立。实测表明：按主键读可在 **40 并发**下满足判据，吞吐上限约 1 500 req/s，瓶颈在应用所在虚机的 CPU（85%~92%），此时数据库仅占用约 19%；写操作只能撑到 **3 并发**，吞吐上限约 106 req/s，瓶颈在 MySQL 所在虚机的 CPU（89.8%）；而同样是"读"的按名称搜索接口更弱，只能撑到 **2 并发**、吞吐上限约 79 req/s，数据库 CPU 高达 93.3%、1 分钟平均负载 13.2。进一步的定量核对显示：三个接口每请求在 InnoDB 层扫描的行数分别约为 **1、34 574、43 211** 行，把"每请求扫描行数 × 吞吐上限"折算成扫描速率后，写与按名搜索分别落在 3.69 M 行/s 与 3.41 M 行/s，接近同一数量级——即这台 2 vCPU 虚机上 MySQL 的扫描能力上限。因此本实验把"读优于写"的结论修正为：**决定性能上限的不是读或写的语义，而是每请求需要扫描的行数**；在本环境下，一个没有索引可用的读接口可以比写接口更慢，而磁盘 I/O 利用率（0.00%~0.37%）与 InnoDB 行锁等待（增量为 0）两项反证排除了刷盘与锁竞争的成因。压测同时暴露出一个与并发无关的功能缺陷：按名称搜索接口有恒定 0.35%~0.42% 的 HTTP 500，源于关联列表为空时未判空即取值。最后给出以二级索引为中心的可执行优化建议。

**关键词**：MySQL；Spring 应用；JPA；JMeter；性能测试；瓶颈定位；全表扫描

**Read and write performance of a MySQL-backed Spring application: locating the real bottleneck**

〔authors — 待填〕

**Abstract** : Database access is commonly the dominant bottleneck of Spring applications backed by MySQL, and "reads are much faster than writes" is the most frequently quoted rule of thumb. This experiment stress-tests three RESTful APIs of one single Spring Boot + JPA application on a four-VM Docker Swarm cluster, using JMeter 5.6.3 in non-GUI mode and a stepwise increase of concurrency, with the criterion that "80% of the requests must respond within 40 ms". Rather than driving load with "threads × loops", each level runs for a fixed duration with iterations set to forever, and the number of simultaneously alive threads recorded in the result file is used to verify that the intended concurrency was actually reached. A primary-key read sustains 40 concurrent users at a throughput ceiling of about 1 500 req/s, limited by the CPU of the application VM (85%–92%) while the database VM stays around 19%. A product creation sustains only 3 concurrent users at about 106 req/s, limited by the CPU of the MySQL VM (89.8%). A read by name — still a read — sustains only 2 concurrent users at about 79 req/s, with the MySQL VM at 93.3% CPU and a 1-minute load average of 13.2. Measured against the MySQL global counters, the three requests scan about 1, 34 574 and 43 211 InnoDB rows respectively; multiplying the rows per request by the throughput ceiling gives 3.69 M and 3.41 M rows/s for the write and the name search, i.e. essentially the same scanning limit of a 2-vCPU database VM. The conclusion therefore revises the rule of thumb: what determines the capacity ceiling is not whether an operation reads or writes, but how many rows each request has to scan — an unindexed read can be slower than a write. Disk I/O utilisation (0.00%–0.37%) and zero InnoDB row-lock waits rule out flushing and lock contention. The load test also uncovered a functional defect unrelated to concurrency: a constant 0.35%–0.42% of HTTP 500 responses from the name search, caused by accessing an empty result list without a check. Optimisation suggestions centred on secondary indexes are given at the end.

**Key words**: MySQL; Spring application; JPA; JMeter; performance testing; bottleneck analysis; full table scan

## 1 问题描述

在数据库驱动的应用中，读写效率是高并发场景下系统性能的关键影响因素。"读显著优于写"是通常的解释口径，其归因多为数据持久化、锁定机制与事务处理。本实验不预设这一结论，而是在同一套硬件、同一个应用进程上，对三个接口分别测出"能够有质量地处理请求"的最大并发，并回答三个问题：

1. 在"80% 的请求响应时间小于 40 ms"这一判据下，三个接口各自能承载多大的并发？吞吐上限分别是多少？
2. 到达上限时，饱和的是哪一台虚机、哪一个进程？如何排除发压端自身与监控端造成的假象？
3. 三个接口每请求的实际数据库开销（扫描行数、SQL 条数）差多少？性能差异能否由它单独解释？

实验在四台华为云 ECS 组成的 Docker Swarm 集群上进行，发压端、应用端、数据库端与监控端分处四台独立虚机，三者的资源占用可以分开测量——这是把瓶颈归因到具体一台机器的基本条件。

## 2 实验分析

### 2.1 实验数据

实验环境由四台虚机组成一个 Swarm 栈，规格见表 1，均为 Ubuntu 18.04.6 LTS、Docker 20.10.21。

**表 1 四台虚机的规格与分工**

| 编号 | 主机 | 内网 IP | 规格 | 分工 |
|---|---|---|---|---|
| A | ecs-0001 | 192.168.0.146 | 2 vCPU / 1.8 GB | Swarm Manager；JDK 21、Maven、JMeter 5.6.3，编译镜像并发起压测 |
| B | ecs-0003 | 192.168.0.38 | 2 vCPU / 1.8 GB | 被测应用 `productdemoaop`（Spring Boot + Spring Data JPA + Druid）唯一落点，监听 8080 |
| C | ecs-0002 | 192.168.0.25 | 2 vCPU / 1.8 GB | MySQL 9.2.0，数据库 `oomall_demo` |
| D | ecs-0004 | 192.168.0.55 | 2 vCPU / 1.8 GB | Prometheus + node-exporter，采集四台虚机与应用侧指标 |

被压的三个接口见表 2，对应仓库 `4. SpringAOP/jmeter/` 下三个测试计划。

**表 2 三个被测接口**

| 简称 | 接口 | 测试计划 | 数据来源 | 鉴权 |
|---|---|---|---|---|
| 读（主键） | `GET /shops/{shopId}/products/{id}` | `ReadProduct.jmx` | `products.csv` 中真实存在的 `(shop_id, id)` 配对 | 不需要（测试计划仍带 `Authorization` 头） |
| 写 | `POST /shops/1/products` | `WriteProduct.jmx` | 固定请求体 | 需要，`Authorization` 头内嵌用户 JSON，不带则 401 |
| 读（按名称） | `GET /products?name={productName}` | `ReadProduct1.jmx` | `name.csv` 中 500 个商品名 | 无（顾客侧公开接口） |

数据库基线（压测开始前与全部测试结束后各核对一次，两次一致）：`goods_product` 3 913 行、`id` 范围 1 550~5 573；`goods_goods` 30 282 行；`goods_onsale` 3 850 行。三张表在 `information_schema.statistics` 中**只有主键索引，没有任何二级索引**（表 3），这一点是本实验全部差异的来源。

**表 3 三张业务表现有的索引**

| 表 | 行数 | 索引 |
|---|---|---|
| `goods_goods` | 30 282 | PRIMARY(`id`) |
| `goods_product` | 3 913 | PRIMARY(`id`) |
| `goods_onsale` | 3 850 | PRIMARY(`id`) |

口径说明：核对 `productdemoaop` 的构建依赖，持久层只有 `spring-boot-starter-data-jpa`，实体以 `@Entity`/`@Table` 映射到上述三张表，因此下文按 Spring Data JPA（Hibernate）描述数据访问层；包名与接口名沿用仓库里的 `mapper`/`PoMapper` 命名，但其中并未引入 MyBatis。

### 2.2 实验方法

先在本地用 JMeter GUI 配置 `xxx.jmx`（主机、并发参数、CSV 数据源、请求内容），再在服务器 A 上以非 GUI 模式运行，结果写入 `xxx.jtl`，并用 `jmeter -e -o` 从 `.jtl` 生成可视化 HTML 报告。

评价使用报告中的两张图：

- **Response Times Over Time**：整个测试期间响应时间的变化趋势。按样例口径，成图时不保留 TCP/IP 连接耗时（Connect Time）序列——本报告全部 Over Time 图取自 JMeter 5.6.3 报告的同名图，该图本身只画 `elapsed` 列，`Connect Time Over Time` 在同一页上是另一张独立图。实测结果文件中 `Connect` 列全部为 0，说明连接被 Keep-Alive 复用，建连耗时确实没有混入响应时间。
- **Response Time Percentiles**：响应时间的分位分布。相比平均响应时间，它更能反映系统在负载下的稳定性。

除读图之外，本实验直接从 `.jtl` 逐行计算三件事，作为图上结论的数值依据：

1. **`<40 ms` 达标比例**——判据要求的是比例，而报告不直接输出 P80，只能自行统计；
2. **同一时刻真实存活的线程数**（`allThreads` 列）——判断"施压是否真的施加到了目标并发"，下一节说明原因；
3. **平均/中位/分位响应时间与吞吐**——统一取自该档报告的 `statistics.json`，与提交的图同源，避免同一指标两处不一致。

需要说明的是，同一份数据自算的分位数与报告的 `statistics.json` 相差 1~4 ms（两者取法不同：最近秩与 JMeter 的百分位聚合），因此表 4~表 6 的分位数与吞吐一律以 `statistics.json` 为准，只有达标比例与 `allThreads` 是自算的。

旁证指标来自服务器 D 上的 Prometheus：四台虚机的 CPU 忙闲与 1 分钟负载、磁盘 I/O 利用率、可用内存，应用侧按 URI 标签拆分的平均响应时间与吞吐、正在处理的请求数、GC 占比与线程数；数据库侧用 MySQL 全局计数器（`Innodb_rows_read`、`Com_select`、`Com_insert`、`Com_commit`、`Innodb_row_lock_waits`）的前后增量折算每请求开销。

### 2.3 实验设计

**响应时间目标值**沿用 40 ms：人类大脑处理视觉输入的速度约为 13~80 ms，落在这一区间内的响应对用户而言即是即时的；同时在 30 FPS 下，一帧时间约 40 ms，该范围内的延迟不会被察觉。在此基础上要求 **80% 的请求响应时间低于 40 ms**——达到该标准说明系统不仅能高效处理请求，还有余量应对当前规模的负载。这一判据等价于"P80 ≤ 40 ms"，工业界的同类指标是 Apdex（把 40 ms 作为 satisfied 阈值即可）。不采用平均响应时间作判据有两个理由：其一，均值把长尾摊平，本实验 40 并发档 avg 24.5 ms 而 max 208 ms，实际有 14.4% 的请求超过阈值，用户撞上的是单个请求而不是期望值；其二，均值随并发平滑上升，而达标比例在临界点附近陡降——若把判据写成"平均值小于 40 ms"，50 并发档（avg 30.8 ms）也会被判定为合格，读的拐点会被误判到 50~75 之间，容量上限高估近一倍。每个档位**至少运行两次**，以排除单次结果的偶然性，表 4~表 6 中同一并发的多行即为独立运行。

**负载控制参数**。JMeter 中关键配置是线程数（thread）、ramp-up 周期与循环数（loop）：线程数决定并发用户数量，ramp-up 决定这些线程在多长时间内创建完毕，循环数决定每个线程重复执行测试的次数。

指导书与多数做法用"线程数 × 循环数"表示请求总数，本实验没有沿用这个口径，原因如下。线程是在 ramp-up 期间逐步创建的，而并发是排队论意义上的稳态量：按 Little 关系，平均并发 ≈（线程数 / ramp-up）×（循环数 × 单次响应时间）。本实验的响应时间在毫秒量级、ramp-up 为秒量级，只有当"循环数 × 响应时间"不小于 ramp-up 时线程池才可能被填满；否则先创建的线程在后创建的线程还没开始工作时就已经跑完退出，真正施加的并发只有个位数，档位名里的线程数并不成立。这不是推算：我们按样例的同一配置（标称 1 200 线程、loop 2、ramp-up 10 s）复跑并读取其结果文件，`allThreads` 峰值只有 **29**，实际吞吐 200~254 req/s。因此本实验把负载控制改为：

- **循环设为 Forever，用固定持续时间收口**（`ThreadGroup` 开启 scheduler，读接口 30 s、写接口 20 s），保证线程在整段测试期间同时存活；
- ramp-up 固定为 5 s，每档**只改变线程数**，逐步逼近；
- 每档测试完成后先看 `allThreads` 是否等于设定线程数，不等于则该档作废重测。本报告表 4~表 6 每一行的峰值并发均与设定值一致。

改用固定时长还带来两项好处：各档观测窗口等长，四台虚机的资源曲线在同一时间尺度上可比；样本数由吞吐决定（约 30 s × 吞吐），慢接口自动少样本、快接口自动多样本，不必为每档手工换算循环数。

**内存与线程上限**。四台虚机只有 1.8 GB 内存，指导书建议的 `-Xmx4g` 无法使用，JMeter 的 JVM 参数调整为 `-Xms256m -Xmx512m -Xss512k -XX:MaxMetaspaceSize=256m`。为提高可开启的线程数，进一步把线程栈减小到 512 KB：每个线程启动时都会分配栈空间，减小栈空间即降低单线程内存占用，从而提高总线程数[2]。实测中 A 机在 300 线程下运行正常，可用内存保持在 1 300 MB 以上，未出现因线程创建失败而中断测试的情况。

**写测试的库状态控制**。写请求会向 `goods_product` 插入新行，而本实验的读、写开销都与表规模成正比（见 2.4.4）。为使各档之间只有"并发"这一个变量，每档写测试结束后删除本实验插入的行（`delete from goods_product where id > 5573`，只按主键边界删除，不 truncate、不动其他表），使下一档从同一基线开始。固定时长模式下仅 20 s 就会写入约 2 000 行，若不逐档清理，整条写阶梯结束时表规模将增至基线的四倍，"并发变高"与"表变大"两个效应混在一起，拐点无法归因。

### 2.4 实验结果与分析

#### 2.4.1 读接口（按主键取商品）

从 10 并发起逐级加到 300 并发，ramp-up 5 s、持续 30 s。结果见表 4，其中 40 并发的三行为同一档的三次独立运行。

**表 4 读阶梯实测结果**（分位数与吞吐取自 `statistics.json`，达标比例与峰值并发自 `.jtl` 统计）

| 并发 | 样本数 | 吞吐(req/s) | avg(ms) | median | p95 | p99 | max | **<40 ms** | 错误率 | 峰值并发 |
|---|---|---|---|---|---|---|---|---|---|---|
| 10 | 41 764 | 1 398 | 6.6 | 5 | 17 | 31 | 156 | **99.56%** ✅ | 0 | 10 |
| 20 | 44 608 | 1 489 | 12.4 | 12 | 29 | 45 | 156 | **98.55%** ✅ | 0 | 20 |
| 30 | 45 197 | 1 506 | 18.3 | 18 | 39 | 55 | 170 | **95.52%** ✅ | 0 | 30 |
| **40** | 45 048 | 1 502 | 24.5 | 24 | 52 | 70 | 208 | **85.58%** ✅ | 0 | 40 |
| 40（复跑） | 48 059 | 1 605 | 22.9 | 22 | 52 | 70 | 152 | **87.30%** ✅ | 0 | 40 |
| 40（复跑） | 45 527 | 1 517 | 24.2 | 24 | 54 | 75 | 225 | **86.22%** ✅ | 0 | 40 |
| **50** | 44 658 | 1 492 | 30.8 | 31 | 66 | 90 | 219 | **71.07%** ❌ | 0 | 50 |
| 75 | 44 795 | 1 487 | 46.3 | 47 | 96 | 125 | 233 | 42.07% ❌ | 0 | 75 |
| 80 | 45 060 | 1 500 | 49.0 | 50 | 102 | 129 | 279 | 39.43% ❌ | 0 | 80 |
| 100 | 44 646 | 1 481 | 62.0 | 62 | 133 | 171 | 348 | 31.51% ❌ | 0 | 100 |
| 200 | 43 590 | 1 431 | 127.9 | 131 | 243 | 295 | 496 | 9.49% ❌ | 0 | 200 |
| 300 | 44 935 | 1 451 | 188.0 | 194 | 369 | 452 | 810 | 7.39% ❌ | 0 | 300 |

- **10 并发**：Response Times Over Time 几乎平直，稳定在 5 ms 附近；Percentiles 曲线上 99.56% 的请求落在 40 ms 内。值得注意的是此时吞吐已达 1 398 req/s，说明系统在很低并发下就已接近其处理能力上限。
- **20 并发**：Over Time 仍稳定，median 升到 12 ms，与并发数成正比；98.55% 达标，吞吐 1 489 req/s。
- **30 并发**：曲线形态不变，median 18 ms；95.52% 达标，吞吐 1 506 req/s 进入平台。
- **40 并发**：RT 分布开始变宽（p99 70 ms、max 208 ms），仍有 85.58% 达标；两次独立复跑得到 87.30% 与 86.22%，三次都在判据之上，偶然性可以排除。
- **50 并发**：median 31 ms 尚可，达标比例降到 71.07%，跌破判据；吞吐没有增加（1 492 req/s），多出来的并发全部转为排队。
- **80 并发**：median 50 ms、p99 129 ms，39.43% 达标，吞吐仍钉在 1 500 req/s。
- **200~300 并发**：median 131 ms / 194 ms，max 810 ms，达标比例不足 10%；但错误率始终为 0，应用侧"正在处理的请求数"峰值 301，恰好等于部署参数中的 `server.tomcat.threads.max=300`——超出部分排在接收队列中等待而不是被拒绝，这解释了为什么塌缩阶段仍然零失败。

**结论**：本系统对按主键的读请求能在 **40 并发**负载下仍保持有质量的处理，是能处理的最大负载临界点；超过这一负载后响应时间显著上升而吞吐不再增加，读的吞吐上限约为 **1 500 req/s**。

图 1~图 8：10 / 40 / 50 / 300 并发各两张，依次为 Response Times Over Time 与 Response Time Percentiles。

【待插图：`步骤6截图/ReadProduct-10-5-30dur__1-ResponseTimesOverTime.png`、`__2-ResponseTimePercentiles.png`；`ReadProduct-40-5-30dur__1/2`；`ReadProduct-50-5-30dur__1/2`；`ReadProduct-300-5-30dur__1/2`（均已生成）】

#### 2.4.2 写接口（创建商品）

从 1 并发起加到 32 并发，ramp-up 5 s、持续 20 s，每档之间清理本档插入的行。结果见表 5，其中 2、3、4、8 并发的两行各为同一档的两次独立运行。

**表 5 写阶梯实测结果**

| 并发 | 样本数 | 吞吐(req/s) | avg(ms) | median | p95 | p99 | max | **<40 ms** | 错误率 | 峰值并发 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 148 | 57.6 | 17.2 | 17 | 19 | 23 | 47 | **99.91%** ✅ | 0 | 1 |
| 2 | 1 706 | 85.6 | 21.8 | 22 | 25 | 30 | 57 | **99.82%** ✅ | 0 | 2 |
| 2（复跑） | 1 645 | 82.5 | 22.6 | 22 | 26 | 31 | 132 | **99.88%** ✅ | 0 | 2 |
| **3** | 1 882 | 94.4 | 29.1 | 28 | 41 | 46 | 53 | **90.97%** ✅ | 0 | 3 |
| **3**（复跑） | 1 878 | 94.2 | 29.1 | 29 | 41 | 47 | 53 | **90.73%** ✅ | 0 | 3 |
| **4** | 1 956 | 98.1 | 36.9 | 38 | 52 | 59 | 76 | **55.73%** ❌ | 0 | 4 |
| **4**（复跑） | 1 947 | 97.6 | 37.1 | 39 | 52 | 60 | 79 | **55.42%** ❌ | 0 | 4 |
| 8 | 2 078 | 104.2 | 68.5 | 73 | 94 | 111 | 130 | 9.43% ❌ | 0 | 8 |
| 8（复跑） | 2 081 | 104.3 | 68.4 | 73 | 94 | 113 | 130 | 9.51% ❌ | 0 | 8 |
| 16 | 2 124 | 106.2 | 133.4 | 145 | 173 | 210 | 246 | 4.90% ❌ | 0 | 16 |
| 32 | 2 141 | 106.8 | 264.1 | 293 | 317 | 336 | 388 | 2.01% ❌ | 0 | 32 |

- **1 并发**：Over Time 平稳在 17 ms 附近，99.91% 达标。值得单独指出的是单次写入本身就要约 17 ms，而同一链路上按主键的读请求在 1 并发探针下 10 s 完成了 6 956 个请求、平均约 1.4 ms（表 7）——完全无排队时两者就差十几倍，说明差距不是并发引起的。
- **2 并发**：吞吐 85.6 req/s，median 22 ms，99.82%（复跑 99.88%）达标。
- **3 并发**：吞吐 94.4 req/s，median 28 ms，p95 已达 41 ms 刚好跨在阈值上，仍有 90.97% 达标；两次运行相差 0.24 个百分点。
- **4 并发**：median 38 ms 看上去仍贴线，但分布已经横跨 40 ms 这条线，只有 55.73% 达标（复跑 55.42%）；吞吐 98.1 req/s 已经接近天花板，加进来的并发几乎全部转为等待。
- **8 并发**：104.2 req/s、median 73 ms，仅 9.43% 达标；独立复跑得到 104.3 req/s、median 73 ms、9.51%，三项与第一次完全一致。
- **16~32 并发**：吞吐钉死在 106~107 req/s，median 随并发线性增长（145 ms、293 ms），达标比例降到 2%；全程零错误，全部返回 201，说明数据库只是慢，并没有丢弃请求。

**结论**：写操作在 **3 并发**下仍能满足判据、4 并发即不满足，是能处理的最大负载临界点；吞吐上限约 **106 req/s**，为读接口的 1/14。

图 9~图 16：1 / 3 / 4 / 32 并发各两张，同上。

【待插图：`WriteProduct-1-5-20dur__1/2`、`WriteProduct-3-5-20dur__1/2`、`WriteProduct-4-5-20dur__1/2`、`WriteProduct-32-5-20dur__1/2`（均已生成）】

#### 2.4.3 读接口（按名称搜索）——无索引对照

这一档是为本实验补充的对照组：与 2.4.1 同一个应用、同一个进程、同一张 `goods_product` 表，唯一区别是查询条件从主键换成 `name` 列，而 `name` 列没有索引。从 1 并发起加到 40 并发，ramp-up 5 s、持续 30 s。结果见表 6。

**表 6 按名称搜索阶梯实测结果**（1 并发两档持续 10 s，其余 30 s）

| 并发 | 样本数 | 吞吐(req/s) | avg(ms) | median | p95 | p99 | max | **<40 ms** | 错误率 | 峰值并发 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1（10 s） | 492 | 49.7 | 19.8 | 18 | 32 | 48 | 60 | **97.36%** ✅ | 0.41% | 1 |
| 1（10 s，复跑） | 484 | 49.2 | 19.9 | 18 | 32 | 58 | 71 | **97.11%** ✅ | 0.41% | 1 |
| **2** | 2 123 | 71.0 | 26.8 | 24 | 44 | 65 | 85 | **89.54%** ✅ | 0.38% | 2 |
| **2**（复跑） | 2 117 | 70.8 | 26.9 | 24 | 44 | 66 | 86 | **89.61%** ✅ | 0.38% | 2 |
| **4** | 2 284 | 76.3 | 49.1 | 46 | 90 | 133 | 192 | **23.29%** ❌ | 0.35% | 4 |
| **4**（复跑） | 2 292 | 76.6 | 48.8 | 46 | 87 | 137 | 181 | **24.69%** ❌ | 0.35% | 4 |
| 10 | 2 357 | 78.8 | 117.5 | 112 | 208 | 303 | 400 | 3.39% ❌ | 0.42% | 10 |
| 10（复跑） | 2 362 | 78.8 | 117.4 | 112 | 207 | 300 | 430 | 3.68% ❌ | 0.42% | 10 |
| 20 | 2 361 | 78.6 | 234.6 | 225 | 412 | 600 | 786 | 1.52% ❌ | 0.42% | 20 |
| 20（复跑） | 2 376 | 79.1 | 233.3 | 224 | 413 | 598 | 788 | 1.56% ❌ | 0.42% | 20 |
| 20（复跑） | 2 372 | 78.5 | 233.6 | 225 | 412 | 601 | 796 | 1.77% ❌ | 0.42% | 20 |
| 40 | 2 378 | 78.9 | 466.0 | 452 | 824 | 1 200 | 1 560 | 0.76% ❌ | 0.42% | 40 |

- **1 并发**：median 18 ms、97.36% 达标，但吞吐只有 49.7 req/s——完全无排队时单次查询就要约 20 ms，是同一链路上主键读（约 1.4 ms）的十几倍，差距同样与并发无关。
- **2 并发**：吞吐 71.0 req/s，median 24 ms，89.54%（复跑 89.61%）达标。两次结果差别不到 0.1 个百分点。
- **4 并发**：达标比例断崖式跌到 23.29%（复跑 24.69%），而吞吐只从 71 涨到 76 req/s；此后 10、20、40 并发吞吐全部停在 78~79 req/s，median 严格按并发数线性增长（112 → 225 → 452 ms），是典型的"服务率已到顶、加线程只加队伍"。用 Little 关系验证：单请求服务时间 1/79 ≈ 12.7 ms，20 并发预测 RT 254 ms、实测 234.6 ms；40 并发预测 508 ms、实测 466.0 ms，吻合。
- **错误率一列需要单独说明**：每档恒为 0.35%~0.42%（40 并发档 2 378 个样本中 10 个 HTTP 500），**与并发数完全无关**，因而不是过载导致的失败。应用日志给出原因：`java.lang.IndexOutOfBoundsException: Index 0 out of bounds for length 0`，抛在 `ProductService.getCustomerProductVo()` 中，即某些商品的关联/上架列表为空时未判空即取下标 0。`name.csv` 有 500 个查询词，40 并发档 2 378 个请求相当于把这 500 个词跑了近 5 轮、共 10 个 500，即每轮约 2 个查询词命中这样的商品，而且每档都是同一批词。这是压力测试暴露出来的功能缺陷，属正确性问题，不计入性能失败；由于数量只占 0.4%，是否剔除对达标比例的影响不足 0.5 个百分点，不影响拐点判断。

**结论**：按名称搜索的并发上限为 **2**，吞吐上限约 **79 req/s**，仅为同一张表上主键读的 1/19；在与 2.4.1 相同并发（40）对照下，主键读 median 24 ms、按名搜索 median 452 ms，相差 19 倍。

图 17~图 22：2 / 4 / 40 并发各两张，同上。

【待插图：`ReadProduct1-2-5-30dur_base__1/2`、`ReadProduct1-4-5-30dur_base__1/2`、`ReadProduct1-40-5-30dur_base__1/2`（均已生成）】

#### 2.4.4 瓶颈定位

**（1）读的上限在应用所在虚机 B 的 CPU。** 吞吐从 10 并发起就锁死在约 1 500 req/s，此时 B 机 CPU 已占用 84.7%，之后各档维持在 85%~92%；与此同时 C 机 MySQL 全程只有 18%~20%，A 机（发压端）只有 21%~25%，D 机（监控端）接近空闲。也就是说：数据库没有饱和，发压端也留有余量，饱和的是应用进程本身。一个主键读请求在数据库侧只是 1 次等值查询、扫描 1 行，1 500 req/s 时 C 机只花约 19% CPU，其余开销都在 JVM 内（Spring MVC 分发、JPA 实体映射、VO 拷贝、AOP 切面）。2 vCPU 打满对应每请求约 1.2 ms CPU 占用，量级合理。

**（2）写的上限在数据库所在虚机 C 的 CPU。** 写档中 C 机 CPU 随并发从 38.9%（1 并发）升到 77%（4 并发）、89.8%（16 并发），而 B 机应用 CPU 始终只有 11%~13%。这与读正好相反：写路径上应用是空闲的，数据库是打满的。

**（3）按名称搜索的上限同样在 C 机，饱和程度不低于写档。** 20 并发档整段窗口内：C 机 CPU 93.3%、1 分钟平均负载 13.2（2 vCPU 上意味着约 13 个任务在 runnable/blocked 队列中堆积），B 机应用 CPU 只有 12.7%、A 机发压端 9.9%、D 机 1.1%；磁盘 I/O 利用率在 C 机为 0.00%、B 机 0.37%；应用 GC 耗时占比 0.00%，JVM 存活线程峰值 315（空闲基线 115），堆用量 160 MB。同档应用侧 Spring 自报的指标同样落在 `/products` 上：平均响应时间 0.23 s、该接口吞吐 74 req/s，与 JMeter 端实测的 233.6 ms、78.5 req/s 基本吻合（Prometheus 的聚合窗口略短于整档，数值有百分之几的出入）。发压端与被测端两套独立统计给出同一个数，说明容量低不是发压机算力不足造成的假象。

**（4）把"慢在哪"落到可核对的量上。** 在 1 并发下各跑 10 s，用 MySQL 全局计数器的前后增量除以请求数，得到每请求在数据库侧的真实开销（表 7）。

**表 7 三个接口每请求的数据库侧开销**（1 并发、10 s、数据库为基线状态）

| 接口 | SELECT/请求 | INSERT/请求 | COMMIT/请求 | InnoDB 扫描行/请求 |
|---|---|---|---|---|
| 读（主键） | 1.00 | 0 | 1 | **1** |
| 写（创建商品） | 3.01 | 1 | 1 | **34 574** |
| 读（按名称） | 4.42 | 0 | 1 | **43 211** |

三张表除主键外没有任何二级索引，按列值过滤的查询只能整表扫描。代码路径清楚地给出扫描次数：写请求 `ProductService.createProduct()` 在 `save()` 之后要把商品组装成返回对象，`ProductDao.getFullProduct()` 附带两个查询——`getLatestOnSale(productId)` 扫 `goods_onsale`、`retrieveOtherProduct()` 用 `findByProductId(id)` 扫 `goods_goods`；按名称搜索的 `ProductDao.retrieveByName()` 先做 `findByName(name, PageRequest.of(0,100))`（扫 `goods_product`），再**对每一条命中记录**调用同一个 `getFullProduct()`。因此：

- 按名搜索每请求的下界 = 3 913 + 30 282 + 3 850 = 38 045 行（三张表各整扫一遍），实测 43 211 行，高出的 13.6% 来自平均命中数略大于 1 时 `getFullProduct` 的重复执行与后续 `findByIdIn` 的回表；
- 写请求实测 34 574 行 ≈ `goods_goods` 30 282 + `goods_onsale` 3 850 = 34 132，余下约 440 行是插入、主键回读与上架列表取值的开销——即写路径的扫描量正好是 `getFullProduct()` 那两个无索引查询各扫一遍；
- 按名搜索比写多出的 8 637 行与 `goods_product` 3 913 + `goods_onsale` 3 850 = 7 763 行量级吻合，多出来的部分就是 `findByName` 对 `goods_product` 的整表扫描与命中数放大后的第二遍组装；
- 主键读每请求 1 行，正说明它没有走这条组装路径。

**（5）三个接口的差异可由扫描行数单独解释。** 把表 4~表 6 的吞吐上限与表 7 的每请求扫描行数相乘，得到数据库的扫描速率：

**表 8 每请求扫描行数折算的扫描速率**

| 接口 | 吞吐上限(req/s) | 扫描行/请求 | 扫描速率(行/s) |
|---|---|---|---|
| 读（主键） | 1 500 | 1 | 1.5 × 10³ |
| 写（创建商品） | 106.8 | 34 574 | 3.69 × 10⁶ |
| 读（按名称） | 78.9 | 43 211 | 3.41 × 10⁶ |

写与按名搜索的扫描速率同为 3.5 × 10⁶ 行/s 量级，即这台 2 vCPU 虚机上 InnoDB 的行扫描能力上限；主键读的扫描量比它们小三个数量级以上，因此它的瓶颈落到了应用 CPU 上。**同一台数据库机器上，两个语义完全不同的接口撞在同一个扫描天花板上，而"读"接口反而比"写"接口容量更低**——按名搜索 79 req/s < 写 106 req/s，因为它每请求多扫 25% 的行。

**（6）排除刷盘与锁竞争。** 若是持久化刷写受限，应看到磁盘 I/O 等待；若是行锁或连接池受限，应看到锁等待与连接池排队。实测三项都是否定的：磁盘 I/O 利用率 0.00%~0.37%，MySQL `Innodb_row_lock_waits` 在全部写测试前后增量为 0，Druid 连接池 `WaitThreadCount` 为 0 且 `Max_used_connections = 277`，没有用到 300 的额度（应用侧 `tomcat.threads.max`、Druid `max-active`、MySQL `max_connections` 三处部署参数同为 300）。

需要说明的是，本实验使用的数据库观测账号没有 `performance_schema` 摘要表的查询权限，Druid 的 SQL 明细接口也返回空，因此没有拿到逐条 SQL 的文本级统计；表 7 是按"请求数 + 全局计数器增量"折算的，其比值都落在容易解释的数上（1、3.01、4.42 条 SELECT），扫描行数的下界又能由表规模独立复算，可信度较高。

#### 2.4.5 与既有测试结果的对照

在同一判据下，本实验的读曲线与样例报告给出的形态不同，且差异可解释。样例的读阶梯为 200~1 200 线程配 10→2 次循环（固定循环数、只加线程），其实际吞吐只有 200~254 req/s，约为本实验测得容量的 1/6，全程响应时间 2~4 ms，从未接近判据——这与 2.3 节的并发模型分析一致：真正施加的并发只有个位数（复跑实测 `allThreads` 峰值 29），因此其"某一线程数为临界点"的结论对应的是发压端未能把线程起满，而不是被测系统的容量。样例的写阶梯发压速率约 100 req/s，恰好压在本实验测得的写容量（106 req/s）上，其响应时间在 104~2 220 ms 之间波动，与样例正文中的 10~20 ms 不符，但与本实验表 5 中 8~32 并发档的形态一致。本实验不对他人结论作进一步推断，仅记录"以 `allThreads` 校验施压是否成立"这一步是必要的。

## 3 总 结

本实验在同一套四虚机环境上，以固定时长驱动真并发的方式，测出了一个 Spring + JPA + MySQL 应用三个接口各自的性能临界点：按主键读可维持 80% 请求小于 40 ms 的并发上限为 **40**，吞吐上限约 1 500 req/s，瓶颈在应用进程的 CPU；写接口的并发上限为 **3**，吞吐上限约 106 req/s，瓶颈在 MySQL 所在虚机的 CPU；按名称搜索的读接口并发上限只有 **2**，吞吐上限约 79 req/s，瓶颈同样在 MySQL 的 CPU（93.3%，1 分钟负载 13.2）。

三个结果放在一起得到的结论是：**在本环境下决定性能上限的不是"读还是写"，而是"每个请求要扫多少行"**。表 7 给出的每请求扫描行数分别是 1、34 574、43 211，与三者的吞吐上限几乎严格反向；写与按名搜索折算出的扫描速率同为 3.5 × 10⁶ 行/s，指向同一个 InnoDB 扫描天花板。这与"读优于写"的常见解释（数据持久化、锁定机制、事务处理）不同——本实验直接测得磁盘 I/O 利用率不足 0.4%、`Innodb_row_lock_waits` 增量为 0、连接池无等待，刷盘与锁竞争都不是原因；真正的结构性原因是 `oomall_demo` 三张业务表除主键外**没有任何二级索引**。按名称搜索这一档特意作为对照加入：它与主键读同表、同应用、同为只读，仅因查询条件无索引就从 1 500 req/s 掉到 79 req/s，证明一个读接口完全可以比写接口更慢。

据此提出三点优化建议，按投入产出排序：

1. **为作为查询条件的列建立二级索引**——`goods_product(name)`、`goods_goods(product_id)`、`goods_onsale(product_id)`。这是投入最小、对写与按名搜索吞吐最直接的一项，可把每请求的扫描行数从 3~4 万降到常数级；本实验未实施（涉及共享库结构变更），建议作为后续验证项。
2. **接口只取必要字段、不级联组装**。写接口插入后回读 VO、按名搜索对每条命中记录再组装完整对象（`getFullProduct`），带来 `goods_goods` 与 `goods_onsale` 的重复整表扫描；列表接口只需摘要字段，应去掉这条组装路径或为其单独定义轻量查询。
3. **读路径在应用侧叠加缓存**。主键读的瓶颈已经在应用 CPU（每请求只扫描 1 行），要抬高 1 500 req/s 的平台值只能减少每请求在 JVM 内的映射与拷贝开销。

方法层面，本实验得到的可复用结论是：以"线程数 × 循环数"表示并发时，必须用结果文件中记录的同时存活线程数加以校验，否则测出的"临界点"可能只描述发压端自身；固定时长 + Forever 循环 + 只变线程数是更直接的逼近方式。此外，压力测试同时暴露了一个与并发无关的功能缺陷（空列表未判空导致的恒定 0.4% HTTP 500），提示性能测试的日志核对环节不应省略。

## 附录 A 数据来源与交付物

**原始数据**：A 机 `/root/spring-fundamentals/4. SpringAOP/jmeter/` 下各 `.jtl` 及同名 HTML 报告目录。表 4~表 6 中每一行对应的文件如下（分位数与吞吐取该档 `statistics.json`，达标比例与峰值并发由 `.jtl` 的 `elapsed`、`allThreads` 列统计）。

| 报告位置 | 原始文件 |
|---|---|
| 表 4 读阶梯 | `ReadProduct-{10,20,30,40,50,75,80,100,200,300}-5-30dur.jtl`；40 的两次复跑为 `_run2`、`_r1` |
| 表 5 写阶梯 | `WriteProduct-{1,2,4,8,16,32}-5-20dur.jtl`；2/4 的复跑为 `_r1`，3 并发两次为 `-5-20dur.jtl` 与 `_r1.jtl`，8 的复跑为 `_run2` |
| 表 6 按名搜索 | `ReadProduct1-<并发>-5-30dur.jtl`，1 并发两档为 `ReadProduct1-1-5-10dur.jtl` 与 `_base.jtl`；其余复跑档带 `_base`、`_ev`、`_ev2` 后缀（2/4/10/20 各 2~3 次，40 仅 1 次） |
| 表 7 每请求开销 | `WriteProduct-1-5-10dur_chkW.jtl`、`ReadProduct-1-5-10dur_chkR.jtl`、`ReadProduct1-1-5-10dur_base.jtl`（484 请求）；计数器为 C 机 `show global status` 压测前后快照之差 |
| 表 8 扫描速率 | 表 4~表 6 的吞吐上限 × 表 7 的每请求扫描行数 |
| 2.4.4 CPU / 负载 / 磁盘 / GC | Prometheus（D 机 9090，经 ssh 转发访问），四台 `node_*` 与应用 `http_server_requests_*`、`jvm_*` 指标 |
| 2.4.4 锁与连接池 | C 机 `show global status like 'Innodb_row_lock_waits'`、Druid 监控页 `datasource.json` |
| 2.4.5 复现样例配置 | `ReadProduct-1200-10-2.jtl`（第 13 列峰值 29） |
| 2.3 线程栈与内存 | A 机 JMeter `setenv` 中 `JAVA_ARGS`；`/root/mem.sh` 输出 |

**截图**：每档两张，取自该档报告目录 `content/pages/` 下的 `OverTime.html#responseTimesOverTime`（Response Times Over Time）与 `ResponseTimes.html#responseTimePercentiles`（Response Time Percentiles），用图页工具条上的 Save as PNG 导出。图 1~图 22 共 22 张已全部生成于 `步骤6截图/`。

**待办**：作者姓名与英文作者名；表 1~表 8 编号如与最终版式冲突可整体平移；`conf/docker/aop-ours.yml` 目前是未纳入版本管理的部署文件，建议提交进组内仓库或作为附件随报告提交（2.4.4 中"三处参数同为 300"的证据来自该文件）。

## References

[1] ERINLE B. Performance testing with JMeter[M]. Packt Publishing Ltd, 2015.
[2] GRUNWALD D, NEVES R. Whole-program optimization for time and space efficient threads[J]. ACM SIGOPS Operating Systems Review, 1996, 30(5): 50-59.

〔2.3 节用到的 Little 关系若要正式列参考文献，需先核对该定律原始论文的年份/卷/页再补 [3]，此处不凭记忆填写。〕
