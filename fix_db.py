import pymysql

print("Connecting to MySQL...")
c = pymysql.connect(host='127.0.0.1', port=3306, user='qos_app', password='QosApp@123', database='qos_user_center')

print("Changing role column to VARCHAR...")
# First check current state
with c.cursor() as x:
    x.execute("DESCRIBE t_user")
    for row in x.fetchall():
        print(row)

# Change to handle any string value
with c.cursor() as x:
    x.execute("ALTER TABLE t_user MODIFY role VARCHAR(32) NOT NULL DEFAULT 'user'")
    c.commit()
    print("Column type changed!")

# Verify
with c.cursor() as x:
    x.execute("DESCRIBE t_user")
    for row in x.fetchall():
        print(row)

c.close()
print("Done!")