import requests, urllib3
urllib3.disable_warnings()

r = requests.get('https://jcyxy.gszy.edu.cn/', verify=False)
with open('ruishu_412.html', 'w', encoding='utf-8') as f:
    f.write(r.text)
print('412 page saved, length:', len(r.text))
