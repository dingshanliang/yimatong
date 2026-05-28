"""任务状态查询 API（已废弃：CSV 导出改为同步流式）"""

from fastapi import APIRouter

task_router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])
