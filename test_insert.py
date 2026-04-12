import pymysql

DB = {"host": "127.0.0.1", "port": 3306, "user": "qos_app", "password": "QosApp@123", "database": "qos_user_center"}
c = pymysql.connect(**DB)
with c.cursor() as x:
    x.execute("INSERT INTO t_user (username,password_hash,role) VALUES ('test23@test.com','test123', CONVERT('net-ops' USING utf8mb4))")
    c.commit()
    print("CONVERT worked!")
c.close()