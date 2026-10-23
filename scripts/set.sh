#!/bin/bash
# 用法: bash /root/sweep/set.sh <tomcatThreadsMax> <druidMaxActive> [minIdle] [initialSize]
Y="/root/spring-fundamentals/4. SpringAOP/conf/docker/productdemoaop-docker.yml"
T=$1; A=$2; I=${3:-$1}; N=${4:-$1}
sed -i "71s#.*#      - \"--server.tomcat.threads.max=$T\"#" "$Y"
sed -i "72s#.*#      - \"--spring.datasource.druid.max-active=$A\"#" "$Y"
sed -i "73s#.*#      - \"--spring.datasource.druid.min-idle=$I\"#" "$Y"
sed -i "74s#.*#      - \"--spring.datasource.druid.initial-size=$N\"#" "$Y"
sed -n '69,75p' "$Y"
cd "$(dirname "$Y")" && docker stack deploy -c productdemoaop-docker.yml lab >/dev/null 2>&1
for i in $(seq 1 30); do
  R=$(docker service ls -f name=lab_productdemoaop --format '{{.Replicas}}')
  [ "$R" = "1/1" ] && sleep 6 && break
  sleep 3
done
echo "replicas=$(docker service ls -f name=lab_productdemoaop --format '{{.Replicas}}')"
docker service inspect lab_productdemoaop --format '{{json .Spec.TaskTemplate.ContainerSpec.Args}}'
