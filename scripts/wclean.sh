#!/bin/bash
# 每档写测试之后把本实验插入的行删掉，让下一档从同一基线开始
# 只按主键边界删除：不 truncate、不动其他表
P=123456
C=$(docker ps -qf name=lab_mysql | head -1)
docker exec -e P="$P" "$C" sh -c 'mysql -udemouser -p"$P" oomall_demo -N -e "select count(*), max(id) from goods_product; delete from goods_product where id > 5573; select count(*), max(id) from goods_product"' 2>&1 | grep -v insecure
