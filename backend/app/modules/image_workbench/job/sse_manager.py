"""绘图工作台 - 绘图任务 SSE 连接管理（设计 D5）

进度按**任务所有者**定向推送（``send_to_user``）：每条连接只收自己提交的任务，
不广播——观测者与本任务无关的进度不该进这条流。
"""

from app.core.sse_manager import SSEConnectionManager

image_workbench_job_manager = SSEConnectionManager(channel="image_workbench_job")
