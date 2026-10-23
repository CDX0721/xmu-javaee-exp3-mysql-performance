#!/bin/bash
# 用法: bash /root/sweep/sweep.sh <备注前缀> —— 3 轮 warmup 后跑两个测量档
tag=$1
for i in 1 2 3; do bash /root/sweep/run.sh ReadProduct 2000 10 1 "warmup-$tag-$i" | sed 's/^/  /'; done
bash /root/sweep/run.sh ReadProduct 4000 4 1 "$tag"
bash /root/sweep/run.sh ReadProduct 8000 8 1 "$tag"
bash /root/sweep/run.sh ReadProduct 2000 2 8 "$tag-loops8"
