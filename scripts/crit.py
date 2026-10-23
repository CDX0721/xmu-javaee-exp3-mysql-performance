# 用法: python3 /root/sweep/crit.py <jtl> [<jtl> ...]   判据: 80% 请求 elapsed<=40ms
import sys, os
TH = 40.0
for f in sys.argv[1:]:
    if not os.path.exists(f):
        continue
    rt, err = [], 0
    with open(f) as fh:
        hdr = fh.readline().strip().split(',')
        ie, it, iok = hdr.index('elapsed'), hdr.index('timeStamp'), hdr.index('success')
        for line in fh:
            p = line.rstrip('\n').split(',')
            if len(p) <= iok:
                continue
            rt.append(int(p[ie]))
            err += (p[iok] != 'true')
    if not rt:
        continue
    rt.sort()
    ts = []
    with open(f) as fh:
        fh.readline()
        for line in fh:
            p = line.split(',')
            if len(p) > it:
                ts.append(int(p[it]))
    span = (max(ts) - min(ts) + rt[-1]) / 1000.0 if ts else 0
    ok = sum(1 for x in rt if x <= TH)
    p80 = rt[min(len(rt) - 1, int(0.80 * len(rt)))]
    print('%-34s n=%-6d err=%-4d lam=%-7.1f frac<=40ms=%5.1f%%  p80=%5d  %s'
          % (os.path.basename(f), len(rt), err, len(rt) / span if span else 0,
             100.0 * ok / len(rt), p80, 'PASS' if 100.0 * ok / len(rt) >= 80 else 'FAIL'))
