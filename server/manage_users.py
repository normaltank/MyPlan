"""One-time local bootstrap for the first administrator.

Run ``python -m server.manage_users create-admin`` in an interactive terminal.
The password is never accepted as a command-line argument or printed.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from server.access_control import AccountStore
from server.agent_queue import AgentTaskQueue


def main() -> int:
    parser = argparse.ArgumentParser(description='高校师资平台账号管理')
    parser.add_argument('command', choices=['create-admin'])
    args = parser.parse_args()
    queue = AgentTaskQueue()
    store = AccountStore(queue.db_path)
    if args.command == 'create-admin' and store.has_admin():
        print('管理员账号已存在；请登录网页创建其他账号。')
        return 1
    username = input('管理员登录名（小写英文，至少 3 位）：').strip()
    display_name = input('显示名称：').strip()
    password = getpass.getpass('密码（至少 12 位，不会显示）：')
    confirm = getpass.getpass('再次输入密码：')
    if password != confirm:
        print('两次输入的密码不一致。')
        return 1
    try:
        store.create_user(username, display_name, password, 'admin')
    except ValueError as exc:
        print(f'创建失败：{exc}')
        return 1
    print('管理员已创建。请登录网页并在管理员面板创建员工账号。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
