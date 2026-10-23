#!/usr/bin/env python3
# 用法: python3 stat.py <jtl文件> [备注]  -> 打印并追加 /root/sweep/results.tsv
import csv, json, os, statistics, sys, urllib.parse, urllib.request
P = "http://192.168.0.55:9090"
URI = "/shops/{shopId}/products/{id}"
CPU = {"cli": "192.168.0.146:9100", "app": "192.168.0.38:9100", "db": "192.168.0.25:9100"}
f = sys.argv[1]; note = sys.argv[2] if len(sys.argv) > 2 else ""
if not os.path.exists(f):
    j = os.path.join("/root/spring-fundamentals/4. SpringAOP/jmeter", f); f = j
def api(path, **kw):
    u = P + path + "?" + urllib.parse.urlencode(kw)
    try: return json.load(urllib.request.urlopen(u, timeout=20)).get("data", {}).get("result", [])
    except Exception: return []
def rng(q, s, e, st=2):
    return [float(y) for x in api("/api/v1/query_range", query=q, start=s, end=e, step=st)
            for _, y in x["values"] if y.lower() != "nan"]
def one(q, t):
    v = [x["value"][1] for x in api("/api/v1/query", query=q, time=t)]
    try: return float(v[0])
    except Exception: return None
rows = []
with open(f, newline="", encoding="utf-8", errors="replace") as fh:
    for x in csv.DictReader(fh):
        try: rows.append((int(x["timeStamp"]), int(x["elapsed"]), int(x["allThreads"]),
                          int(x["Connect"]), int(x["Latency"]), x["success"] == "true",
                          int(x["timeStamp"]) + int(x["elapsed"])))
        except Exception: pass
n = len(rows)
if n == 0:
    print("empty jtl"); sys.exit(1)
t0 = min(r[0] for r in rows) / 1000.0; t1 = max(r[6] for r in rows) / 1000.0
rt = sorted(r[1] for r in rows); err = sum(1 for r in rows if not r[5])
span = t1 - t0
q = lambda p: rt[min(n - 1, int(n * p))]
S, E = t0 - 5, t1 + 15
win = int(max(10, (E - S)))
srvq = ('sum(increase(http_server_requests_seconds_sum{uri="%s"}[%ds]))/'
        'sum(increase(http_server_requests_seconds_count{uri="%s"}[%ds]))' % (URI, win, URI, win))
srv = one(srvq, E)
srvn = one('sum(increase(http_server_requests_seconds_count{uri="%s"}[%ds]))' % (URI, win), E)
thr = rng("jvm_threads_live_threads", S, E); inf = rng("sum(http_server_requests_active_seconds_count)", S, E)
gc = one("increase(jvm_gc_pause_seconds_sum[%ds])" % win, E)
of = rng("process_files_open_files", S, E)
cpu = {k: rng('100*(1-avg(rate(node_cpu_seconds_total{instance="%s",mode="idle"}[5s])))' % v, S, E) for k, v in CPU.items()}
mx = lambda a: round(max(a), 1) if a else ""
mn = lambda a: round(statistics.mean(a), 1) if a else ""
lam = n / span if span > 0 else n
rec = [os.path.basename(f), note, n, err, round(lam, 1), round(span, 1), q(.5), q(.9), q(.95), q(.99), rt[-1],
       round(statistics.mean(r[2] for r in rows), 2), max(r[2] for r in rows),
       round(statistics.mean(r[3] for r in rows), 1), round(statistics.mean(r[4] for r in rows), 1),
       round(srv * 1000, 1) if srv is not None else "", round(srvn) if srvn is not None else "",
       mn(thr), mx(thr), mx(inf), round(gc, 3) if gc is not None else "", mn(of),
       mx(cpu["cli"]), mx(cpu["app"]), mx(cpu["db"])]
hdr = ["tier", "note", "n", "err", "lam", "span", "p50", "p90", "p95", "p99", "maxRT", "at_avg", "at_max",
       "connect", "latency", "srv_rt_ms", "srv_cnt", "jvmthr_avg", "jvmthr_max", "inflight_max",
       "gc_s", "open_files", "cpu_cli%", "cpu_app%", "cpu_db%"]
p = "/root/sweep/results.tsv"
isnew = not os.path.exists(p)
with open(p, "a") as fh:
    if isnew: fh.write("\t".join(hdr) + "\n")
    fh.write("\t".join(str(x) for x in rec) + "\n")
print(" | ".join("%s=%s" % (h, v) for h, v in zip(hdr, rec)))
