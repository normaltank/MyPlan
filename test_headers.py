import requests
import urllib3
urllib3.disable_warnings()

headers = {
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0',
    'sec-ch-ua': '"Microsoft Edge";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"'
}
r = requests.get('https://jcyxy.gszy.edu.cn/', headers=headers, verify=False)
print('status with HeadlessChrome + sec-ch-ua:', r.status_code)

# Now test without sec-ch-ua
headers2 = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}
r2 = requests.get('https://jcyxy.gszy.edu.cn/', headers=headers2, verify=False)
print('status with standard Chrome:', r2.status_code)
