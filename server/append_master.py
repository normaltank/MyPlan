import sys
import os
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')

import build_dataset as b
new_records = b.unique_teachers

master_file = '教师列表.xlsx'
df_master = pd.read_excel(master_file)
columns = ['序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页']

df_new = pd.DataFrame(new_records)
for col in columns:
    if col not in df_new.columns:
        df_new[col] = ''
df_new = df_new[columns]

# Append
combined = pd.concat([df_master, df_new], ignore_index=True)
combined['序号'] = range(1, len(combined) + 1)

combined.to_excel(master_file, index=False)
print(f"Master file updated: original={len(df_master)}, new={len(df_new)}, total={len(combined)}")
