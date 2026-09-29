"""The double-click Web launcher must serve task status from its own cwd."""
import os
from pathlib import Path
import subprocess
import sys

from server import app as web


def test_launcher_package_import_from_server_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env['HARVEST_TASK_DB'] = str(tmp_path / 'tasks.db')
    code = '''from start_server import load_web_app
web_app = load_web_app()
from server.agent_queue import agent_queue
from server.access_control import AccountStore
from fastapi.testclient import TestClient
AccountStore(agent_queue.db_path).create_user('admin','Administrator','correct horse battery staple','admin')
task = agent_queue.submit_task('测试大学', '测试学院', 'https://example.edu')
client = TestClient(web_app)
assert client.post('/api/auth/login', json={'username':'admin','password':'correct horse battery staple'}).status_code == 200
response = client.get('/api/task/' + task.task_id)
assert response.status_code == 200, response.text
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=root / 'server',
                            env=env, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr


def test_claimed_task_shows_recon_started_while_progress_is_unknown():
    task = {'status': 'running', 'current_step': '工单已进入队列，等待智能体接单...',
            'download_url': None}
    scope = {'state': 'collecting', 'identity_conflicts': []}
    shown = web.present_task(task, scope)
    assert shown['current_step'] == '后台已接单，正在核实官网结构'
