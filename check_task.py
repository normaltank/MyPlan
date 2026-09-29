import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('server/tasks.db')
cursor = conn.cursor()
for row in cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
    print(row)
print("---")
for row in cursor.execute("PRAGMA table_info(tasks)"):
    print(row)
print("---")
for row in cursor.execute("SELECT task_id, school, college, url, status, stats_json, download_url FROM tasks WHERE school LIKE '%甘肃中医药%'"):
    print(row)
