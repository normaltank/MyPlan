import sys
sys.stdout.reconfigure(encoding='utf-8')

from ide_dispatch_runner import export_university_excel
import build_dataset as b

records = b.unique_teachers
out_file = export_university_excel("甘肃中医药大学", records)
print(f"Exported university excel: {out_file}")
