#!/bin/bash
# 用法: bash /root/sweep/run.sh <jmx> <M> <T> <loop> <备注>
# 产物仍按老师的 <jmx>-<M>-<T>-<loop> 命名；跑完额外复制一份到 /root/sweep/keep/<备注>--<原名>
# 因为同名档位换配置重跑会覆盖 jtl，keep/ 里的归档才是可追溯的
export JAVA_HOME=/root/jdk-21.0.2
export PATH="$JAVA_HOME/bin:/root/apache-jmeter-5.6.3/bin:$PATH"
cd "/root/spring-fundamentals/4. SpringAOP/jmeter" || exit 1
name=$1-$2-$3-$4
note=${5:-untagged}
mkdir -p /root/sweep/keep
rm -rf "$name" "$name.jtl"
sh jmeter-command.sh "$1" "$2" "$3" "$4" >/dev/null 2>&1
sleep 12
[ -f "$name.jtl" ] && cp "$name.jtl" "/root/sweep/keep/${note}--${name}.jtl"
[ -d "$name" ] && rm -rf "/root/sweep/keep/${note}--${name}" && cp -r "$name" "/root/sweep/keep/${note}--${name}"
python3 /root/sweep/stat.py "$name.jtl" "$note"
