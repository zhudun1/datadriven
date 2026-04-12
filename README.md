# 本机开隧道
ssh -i ~/.ssh/cardiff_root -p 6003 root@210.30.97.61 -N \
  -L 8080:127.0.0.1:8080 \
  -L 8000:127.0.0.1:8000

# 启动后端
export QOS_MYSQL_HOST=127.0.0.1
export QOS_MYSQL_PORT=3306
export QOS_MYSQL_USER=root
export QOS_MYSQL_PASSWORD='123456'
export QOS_MYSQL_DATABASE=qos_user_center

cd /root/data-driven_2
conda activate py310
python3 backend_server.py

# 启动前端
cd /root/data-driven_2
conda activate py310
cd /root/data-driven_2/frontend
python3 -m http.server 8080

# 前端页面url
# 登录
http://127.0.0.1:8080/index.html
# 注册
http://127.0.0.1:8080/register.html
# 主页面
http://127.0.0.1:8080/sandbox/index.html

# 测试数据
/root/data-driven_2/datasets/mvtec3d/cookie/test/good/rgb/000.png
/root/data-driven_2/datasets/mvtec3d/cookie/test/good/xyz/000.tiff