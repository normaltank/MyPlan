"""The optional queue monitor counts local work without authentication or writes."""
from pathlib import Path
import sqlite3
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "server/auto_dispatcher.py"


def test_monitor_counts_only_queued_colleges_without_changing_database(tmp_path):
    path = tmp_path / "tasks.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tasks (status TEXT, task_type TEXT)")
        connection.executemany("INSERT INTO tasks VALUES (?,?)", [
            ("queued", "harvest"), ("queued", "harvest"), ("queued", "compile_ticket"),
            ("running", "harvest"), ("failed", "harvest")])
    original, modified_at = path.read_bytes(), path.stat().st_mtime_ns
    result = subprocess.run([sys.executable, str(SCRIPT), "--once", "--db-path", str(path),
                             "--url", "http://127.0.0.1:18000"],
                            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stderr
    assert "2 个学院工单" in result.stdout
    assert "http://127.0.0.1:18000" in result.stdout
    assert path.read_bytes() == original and path.stat().st_mtime_ns == modified_at
    assert set(tmp_path.iterdir()) == {path}


def test_monitor_missing_database_does_not_create_it(tmp_path):
    path = tmp_path / "missing/tasks.db"
    result = subprocess.run([sys.executable, str(SCRIPT), "--once", "--db-path", str(path)],
                            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stderr
    assert "尚未创建" in result.stdout
    assert not list(tmp_path.iterdir())


def test_monitor_default_follows_environment_database_without_queue_initialization(tmp_path, monkeypatch):
    import os
    path = tmp_path / "tasks.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tasks (status TEXT, task_type TEXT)")
        connection.execute("INSERT INTO tasks VALUES ('queued','harvest')")
    monkeypatch.setenv("HARVEST_TASK_DB", str(path))
    original = path.read_bytes()
    result = subprocess.run([sys.executable, str(SCRIPT), "--once"], env=os.environ.copy(),
                            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stderr
    assert "1 个学院工单" in result.stdout
    assert path.read_bytes() == original
    assert set(tmp_path.iterdir()) == {path}
