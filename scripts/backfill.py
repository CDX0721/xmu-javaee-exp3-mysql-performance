# 事后补取主机级指标：每个归档 jtl 按自己的时间窗取峰值；另存一份整晚连续序列用于画图
# 用法: python3 /root/sweep/backfill.py   （只读 Prometheus，不碰服务）
import json, os, glob, urllib.request, urllib.parse
P = "http://192.168.0.55:9090"
NAME = {"192.168.0.146:9100": "A压", "192.168.0.38:9100": "B应",
        "192.168.0.25": "C库", "192.168.0.55:9100": "D监"}
SEL = 'instance=~"192.168.0.(146|38|25|55):9100"'
EX = {"cpu":   '100*(1-avg by(instance)(rate(node_cpu_seconds_total{%s,mode="idle"}[5s])))' % SEL,
      "mem":   '100*(1-node_memory_MemAvailable_bytes{%s}/node_memory_MemTotal_bytes{%s})' % (SEL, SEL),
      "io":    '100*sum by(instance)(rate(node_disk_io_time_seconds_total{%s}[5s]))' % SEL,
      "load1": 'node_load1{%s}' % SEL}
SHORT = {"192.168.0.146:9100": "A压", "192.168.0.38:9100": "B应",
         "192.168.0.25:9100": "C库", "192.168.0.55:9100": "D监"}

def qrange(expr, t0, t1, step):
    u = P + "/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": expr, "start": t0, "end": t1, "step": step})
    return json.load(urllib.request.urlopen(u, timeout=40))["data"]["result"]

def windows(f):
    ts = []
    with open(f) as fh:
        fh.readline()
        for line in fh:
            p = line.split(',')
            if len(p) > 1 and p[0].isdigit():
                ts.append(int(p[0]) / 1000.0)
    return (min(ts), max(ts)) if len(ts) > 1 else None

tiers = []
for f in sorted(glob.glob("/root/sweep/keep/*.jtl")):
    b = os.path.basename(f)[:-4]
    tag = b.split("--")[0] if "--" in b else "legacy"
    w = windows(f)
    if w: tiers.append((tag, b, w[0], w[1]))

hdr = ["tier", "startUTC", "dur_s"] + ["%s_%s" % (k, s) for k in EX for s in ["A压", "B应", "C库", "D监"]]
rows = []
for tag, b, t0, t1 in tiers:
    r = {"tier": b, "dur_s": round(t1 - t0, 1),
         "startUTC": __import__("time").strftime("%H:%M:%S", __import__("time").gmtime(t0))}
    for k, expr in EX.items():
        for m in qrange(expr, t0 - 2, t1 + 2, 2):
            ins = SHORT.get(m["metric"]["instance"], m["metric"]["instance"])
            v = [float(val) for _, val in m["values"]]
            if v: r["%s_%s" % (k, ins)] = round(max(v), 1)
    rows.append(r)

with open("/root/sweep/results-host.tsv", "w") as fh:
    fh.write("\t".join(hdr) + "\n")
    for r in rows:
        fh.write("\t".join(str(r.get(h, "")) for h in hdr) + "\n")

# 整晚连续序列（用于画图）：只取这轮七个正经 tag 的并集窗口
legit = [t for t in tiers if t[0] != "legacy" and t[0] != "ladder-default"] + \
        [t for t in tiers if t[0] == "ladder-default" and t[3] - t[2] < 60]
T0 = min(t[2] for t in legit) - 60; T1 = max(t[3] for t in legit) + 60
step = max(2, int((T1 - T0) / 1500))
out = {"window": [T0, T1], "step": step, "metrics": {}}
for k, expr in EX.items():
    out["metrics"][k] = [{"instance": m["metric"]["instance"],
                          "values": [[float(r), float(v)] for r, v in m["values"]]}
                         for m in qrange(expr, T0, T1, step)]
with open("/root/sweep/series/overnight.json", "w") as fh:
    json.dump(out, fh)
print("rows=%d  overnight window=%.0f..%.0f step=%ds  span=%.0fs"
      % (len(rows), T0, T1, step, T1 - T0))
