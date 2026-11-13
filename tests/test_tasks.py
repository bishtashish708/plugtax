import shutil

import pytest
from tokentrial.tasks import load_tasks, materialize, run_check


def _tasks(root):
    return {t.id: t for t in load_tasks(root)}


def test_builtin_tasks_load(builtin_tasks_root):
    tasks = _tasks(builtin_tasks_root)
    assert {"ping", "py-slug-fix", "config-precedence", "py-cli-flag"} <= set(tasks)
    assert "overhead" in tasks["ping"].tags


@pytest.mark.parametrize("task_id", ["py-slug-fix", "config-precedence", "py-cli-flag"])
def test_start_fails_and_solution_passes(task_id, builtin_tasks_root, tmp_path):
    if not shutil.which("git"):
        pytest.skip("git needed")
    task = _tasks(builtin_tasks_root)[task_id]
    assert not run_check(task, materialize(task, tmp_path / "start"))
    assert run_check(task, materialize(task, tmp_path / "solved", with_solution=True))


def test_ping_always_passes(builtin_tasks_root, tmp_path):
    task = _tasks(builtin_tasks_root)["ping"]
    assert run_check(task, materialize(task, tmp_path / "ws"))
