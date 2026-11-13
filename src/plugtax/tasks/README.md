# Built-in tasks

Small, fast tasks in the TokenTrial task format (`task.toml`, `repo/`, `check.sh`,
optional `hidden_tests/` and `solution/`). Checks only need `python3` and `bash`.

| task | what it exercises |
|---|---|
| `ping` | one-word reply, no tools: the component's fixed always-on overhead |
| `py-slug-fix` | debug a failing unit test |
| `config-precedence` | read code and config to answer a question |
| `py-cli-flag` | add a small feature and a test |

Use your own tasks with `plugtax profile ... --tasks-dir DIR`.
