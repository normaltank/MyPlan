import requests
import urllib3
urllib3.disable_warnings()

# Test with the cookies from playwright
cookies = {
    'f6yfSYoneZAYO': 'qaYRsQ-_L8vkS6ArWPWLWsGTJxJjLzku_RsTy2z8ZN6u2hI6g02UU-wDVf78ysHvDW44v034UcrtPQGyiwqhqamVwwBbjXcDKONNu2PdkSSyV',
    'f6yfSYoneZAYP': 'arRBxWf7PEJrX29MPBAc7lQSVM05HWw3y1KAfMuVpt4cnlq_sHToxIy1XWQNQ88VADRJRKRo5jubCeeyxI1NLticAuccbKLc3vnoDyrXQELtUJ1KYfe9lQ_BtHzr974lDfgCBgtANBwH15icgCJUOmb0IBZpE4aIyyUTDmKaAR6CT-1cMCSnR2liQo_NApa9oee0qHDGbSBbNABPF6LeDAYrwjo013_Sg4TWCsWywwmQuy7h_9L994lOYPD06L2YvFfdURmGh8rUK5D7C26FuJL32XgcRlvWz1OdzzCyqwfeMLhc5_-50N3XlEPb6MhhCb-8rVbZg_uXP4opYzQ2W-G'
}

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0'
}

r = requests.get('https://jcyxy.gszy.edu.cn/', cookies=cookies, headers=headers, verify=False)
print('status with cookies:', r.status_code)
print('text preview:', r.text[:300])
