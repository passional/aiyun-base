"""渠道评估运行 SSE 连接管理（进度推送，D4）"""

from app.core.sse_manager import SSEConnectionManager

channel_eval_run_manager = SSEConnectionManager(channel="channel_eval_run")
