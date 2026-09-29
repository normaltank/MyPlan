import json

with open('processed_teachers.json', 'r', encoding='utf-8') as f:
    teachers = json.load(f)

with_email = [t for t in teachers if t['邮箱']]
no_email = [t for t in teachers if not t['邮箱']]

with open('no_email_names.txt', 'w', encoding='utf-8') as f:
    for t in no_email:
        f.write(f"{t['职称']} - {t['姓名']} - {t['主页']}\n")

with open('with_email_names.txt', 'w', encoding='utf-8') as f:
    for t in with_email:
        f.write(f"{t['职称']} - {t['姓名']}: {t['邮箱']}\n")

print(f"With email: {len(with_email)}")
print(f"No email: {len(no_email)}")
