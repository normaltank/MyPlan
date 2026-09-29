import pandas as pd
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

master_path = '教师列表.xlsx'
sheets = pd.read_excel(master_path, sheet_name=None)
print("Existing sheets:")
for s, df in sheets.items():
    print(f"  {s}: {len(df)} rows")

sheet_names = list(sheets.keys())
s1_name = '教师列表'
s2_name = '无邮箱教师列表'

df_s1 = sheets[s1_name]
df_s2 = sheets[s2_name]

# Check if 海外合作学院 already in master
existing_shisu = df_s2[(df_s2['学校'] == '上海外国语大学') & (df_s2['学院'] == '海外合作学院')]
print(f"Existing 海外合作学院 rows in Sheet 2: {len(existing_shisu)}")

# Read our newly generated table
new_path = 'server/outputs/上海外国语大学_海外合作学院_20260921_093839.xlsx'
new_sheets = pd.read_excel(new_path, sheet_name=None)
new_s2_name = [s for s in new_sheets.keys() if '无邮箱' in s][0]
new_df_s2 = new_sheets[new_s2_name]

if len(existing_shisu) == 0:
    # Append
    combined_s2 = pd.concat([df_s2, new_df_s2], ignore_index=True)
    # Re-index 序号
    combined_s2['序号'] = range(1, len(combined_s2) + 1)
    
    new_s1_label = f"有邮箱学者 (共 {len(df_s1)} 位)"
    new_s2_label = f"无邮箱学者 (共 {len(combined_s2)} 位)"
    
    with pd.ExcelWriter(master_path, engine='openpyxl') as writer:
        df_s1.to_excel(writer, sheet_name=s1_name, index=False)
        combined_s2.to_excel(writer, sheet_name=s2_name, index=False)
    
    print(f"Merged into master successfully! Total master rows: {len(df_s1) + len(combined_s2)}")
    print(f"  {s1_name}: {len(df_s1)} rows")
    print(f"  {s2_name}: {len(combined_s2)} rows")
else:
    print("Already exists in master table.")
