"""运行任务运行时注册表（进程内，单实例部署）"""


class RunTaskRegistry:
    """维护运行中任务的取消标志（不持久化；重启后由启动恢复钩子收敛状态）。"""

    def __init__(self) -> None:
        self._cancel_flags: dict[int, bool] = {}

    def mark_cancel(self, run_id: int) -> None:
        self._cancel_flags[run_id] = True

    def is_canceled(self, run_id: int) -> bool:
        return self._cancel_flags.get(run_id, False)

    def clear(self, run_id: int) -> None:
        self._cancel_flags.pop(run_id, None)


run_task_registry = RunTaskRegistry()
