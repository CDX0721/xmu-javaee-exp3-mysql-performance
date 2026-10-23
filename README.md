# JavaEE 平台技术 实验三：MySQL 读写性能测试与瓶颈定位

厦门大学《JavaEE 平台技术》实验三。在同一套 4 × 2 vCPU 华为云 ECS Docker Swarm 集群上，用课程提供的 JMeter 5.6.3 循环模式脚本对 Spring Boot + JPA 应用的读（按主键查商品）、写（创建商品）两个 RESTful 接口阶梯加压、监控定位、参数消融与 CPU 配额对照。

- 作者：陈泓冰（22920242203172）
- 课程：JavaEE 平台技术（2026 秋）
- 完成日期：2026-10-23

## 核心结论

| 接口 | 最高通过档（80% RT < 40 ms） | 吞吐上限 | 瓶颈 |
|---|---|---|---|
| 读（按主键） | **M = 6500**（98.6% 达标） | ~1000 req/s | 应用虚机 B 的 2 vCPU CPU（97%~99.5%） |
| 写（创建商品） | **M = 950**（90.0% 达标） | ~106 req/s | 数据库虚机 C 的 2 vCPU CPU（100%，load1 峰值 146） |

- 容量上限 = 可用 CPU 时间 ÷ 每请求 CPU 成本（读 1.9~2.0 ms/req，与并发无关），与 Tomcat 最大线程数、Druid 连接池、MySQL max_connections 均无关：三层参数从 32 扫到 1000、或三层同时放大到 1000，上限纹丝不动；线程/连接参数只决定队列出现在哪一层。
- 应用容器 CPU 配额砍半（`--limit-cpu 1`），读上限立即砍半（1020 → 503 req/s，比值 1.9）——决定性反证。
- 判据、阶梯设计、爬坡量化、参数指纹等完整方法与分析见 `report/实验三-实验报告.docx`。

## 目录结构

```
report/    实验报告（docx + markdown 源）
data/      JMeter 原始数据：49 份 .jtl（keep/，文件名含备注前缀）、
           每档统计 results.tsv、四机资源档内峰值 results-host.tsv、
           HTML 报告打包 html-reports.zip、原始归档 sweep-20261023.tar.gz
figures/   JMeter 报告截图（jtl/，368 张）与 Prometheus 查询界面截图（14 张）
program/   实验程序（课程 spring-fundamentals 的 4. SpringAOP 工程 +
           clonefactory，剔除构建产物与数据文件；jmeter 测试计划在
           4. SpringAOP/jmeter-cfg/）
scripts/   压测与消融脚本：run.sh（压一档+归档）、set.sh（改三层参数+部署）、
           stat.py / crit.py（统计与判据）、backfill.py / host.py（Prometheus
           档内峰值回查）、wclean.sh（写档清库）
docs/      补充记录：参数消融与 CPU 配额对照的完整实测过程
```

## 复现要点

```bash
# 1) 预热（重新部署后 JIT 是冷的，首档 p95 虚高 ~75 倍，必须先跑 3 轮）
bash scripts/run.sh ReadProduct 2000 10 1 warm1
# 2) 跑一档并自动归档到 keep/<备注>--<原名>
bash scripts/run.sh ReadProduct 8000 10 1 my-note
# 3) 判据（80% 响应 < 40 ms）
python3 scripts/crit.py /root/sweep/keep/my-note--ReadProduct-8000-10-1.jtl
# 4) 写测试每档之后清库（回到基线 3913 行）
bash scripts/wclean.sh
# 5) 决定性对照：应用容器 CPU 配额砍半
docker service update --limit-cpu 1 lab_productdemoaop
```

注意：`end_input` 等 JMeter/Prometheus 的坑、临界档的施加速率悬崖机制，见报告 2.2/2.3 节与 `docs/`。
