# 用法: python3 /root/sweep/host.py <jtl> [<jtl> ...]
# 按每个 jtl 的首末样本时间戳开一个窗口，回到 Prometheus 取那段时间四台机器的 CPU/内存/磁盘IO/load1 峰值
import json, sys, os, urllib.request, urllib.parse
P = "http://192.168.0.55:9090"
NAME = {"192.168.0.146:9100":"A发压","192.168.0.38:9100":"B应用","192.168.0.25:9100":"C数据库","192.168.0.55:9100":"D监控"}
SEL = 'instance=~"192.168.0.(146|38|25|55):9100"'
def q(expr, t):
    u = P + "/api/v1/query?" + urllib.parse.urlencode({"query": expr, "time": t})
    r = json.load(urllib.request.urlopen(u, timeout=25))
    d = {}
    for m in r.get("data", {}).get("result", []):
        try: d[m["metric"].get("instance","-")] = float(m["value"][1])
        except: pass
    return d
EXPR = {
 "cpu":  '100*(1-max_over_time(avg by(instance)(rate(node_cpu_seconds_total{%s,mode="idle"}[10s]))[{W}s]))',
 "mem":  'max_over_time((100*(1-node_memory_MemAvailable_bytes{%s}/node_memory_MemTotal_bytes{%s})))[{W}s]',
 "io":   '100*max_over_time(sum by(instance)(rate(node_disk_io_time_seconds_total{%s}[10s]))[{W}s])',
 "load1":'max_over_time(node_load1{%s}[{W}s])',
}
out = []
for f in sys.argv[1:]:
    if not os.path.exists(f): continue
    ts = []
    with open(f) as fh:
        fh.readline()
        for line in fh:
            p = line.split(',')
            if len(p) > 1: ts.append(int(p[0]))
    if not ts: continue
    t0, t1 = min(ts)/1000.0, max(ts)/1000.0
    W = int(max(t1-t0, 10) + 8)
    row = {"tier": os.path.basename(f), "dur_s": round(t1-t0, 1)}
    for k, tpl in EXPR.items():
        expr = tpl.replace("{W}", str(W))
        if "%s" in expr:
            expr = expr % tuple([SEL]*expr.count("%s"))
        d = q(expr, t1)
        for ins, v in d.items():
            row["%s_%s" % (k, NAME.get(ins, ins))] = round(v, 1)
    out.append(row)
hdr = sorted({k for r in out for k in r})
print("\t".join(hdr))
for r in out:
    print("\t".join(str(r.get(h, "")) for h in hdr))
