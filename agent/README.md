# Agent

The agent collects host utilization, Docker state and PostgreSQL statistics from one trusted Linux VPS. Start at `src/glorify_control_agent/scheduler.py`; spool/retry behavior is in `storage/spool.py` and `transport/retry.py`.

The examples are generated fictional payloads, not captures. Run `python -m pytest agent/tests -q` after installing the root README's locked dependencies. Runtime settings are in `config.py`; a live installation needs private environment configuration outside this repository. The Docker socket is privileged and SQL statistics may contain sensitive text. See the [security model](../docs/security.md).
