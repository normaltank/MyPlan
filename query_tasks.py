import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('server/tasks.db')
c = conn.cursor()
c.execute("PRAGMA table_info(tasks)")
print(c.fetchall())
c.execute("SELECT * FROM tasks WHERE school LIKE '%甘肃%'")
for r in c.fetchall():
    print(r)
