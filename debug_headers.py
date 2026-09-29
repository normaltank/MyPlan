import requests
import urllib3
urllib3.disable_warnings()

# Curl headers:
# curl sends: Host, User-Agent, Accept: */*
r_curl = requests.get('https://jcyxy.gszy.edu.cn/', headers={'User-Agent': 'curl/8.4.0', 'Accept': '*/*'}, verify=False)
print('Curl-like:', r_curl.status_code)

# Browser headers:
headers_chrome = {
    'Host': 'jcyxy.gszy.edu.cn',
    'Connection': 'keep-alive',
    'sec-ch-ua': '"Microsoft Edge";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'Upgrade-Insecure-Requests': '1',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-User': '?1',
    'Sec-Fetch-Dest': 'document',
    'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
}
r_chrome = requests.get('https://jcyxy.gszy.edu.cn/', headers=headers_chrome, verify=False)
print('Full Chrome headers:', r_chrome.status_code)
