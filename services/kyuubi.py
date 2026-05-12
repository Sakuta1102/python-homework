import os
import time
from typing import Any

from TCLIService.ttypes import TOperationState
from pyhive import hive

_POLL_INTERVAL_S = 1.0

HOST = "kyuubi.bi.moontontech.net"
PORT = 10009


def _extract_kyuubi_error(exc: Exception) -> str:
    """从 pyhive/Thrift 异常中提取可读的错误消息。"""
    for arg in (exc.args or []):
        # TExecuteStatementResp / TGetOperationStatusResp 对象
        if hasattr(arg, "status"):
            s = arg.status
            if getattr(s, "errorMessage", None):
                return s.errorMessage
            if getattr(s, "infoMessages", None):
                return " | ".join(s.infoMessages)
        if isinstance(arg, str) and arg:
            return arg
    return str(exc)

_SPARK_CONFIG = {
    "kyuubi.engine.type": "SPARK_SQL",
    "spark.yarn.queue": "adhoc_ml",
    "spark.sql.shuffle.partitions": "1000",
    "spark.dynamicAllocation.maxExecutors": "100",
    "spark.executor.cores": "4",
    "spark.executor.memory": "14g",
    "spark.executor.memoryOverhead": "8g",
    "kyuubi.operation.incremental.collect": "true",
}


class KyuubiClient:
    def __init__(self) -> None:
        self._username = os.getenv("KYUUBI_USERNAME", "")
        self._password = os.getenv("KYUUBI_PASSWORD", "")

    def run_query(self, sql: str, task_name: str = "") -> list[dict[str, Any]]:
        print(f"[Kyuubi] 连接 {HOST}:{PORT} 执行：{task_name}", flush=True)

        conn = hive.connect(
            host=HOST,
            port=PORT,
            username=self._username,
            auth="LDAP",
            password=self._password,
            configuration=_SPARK_CONFIG,
        )
        cursor = conn.cursor()

        try:
            cursor.execute(sql, async_=True)
        except Exception as e:
            err = _extract_kyuubi_error(e)
            print(f"[Kyuubi ERROR] {task_name} 提交失败: {err}", flush=True)
            raise RuntimeError(err) from None

        status = cursor.poll().operationState
        while status in (TOperationState.INITIALIZED_STATE, TOperationState.RUNNING_STATE):
            for msg in cursor.fetch_logs():
                print(f"[Kyuubi] {msg}", flush=True)
            time.sleep(_POLL_INTERVAL_S)
            status = cursor.poll().operationState

        if status == TOperationState.ERROR_STATE:
            poll = cursor.poll()
            err = (getattr(poll, "errorMessage", None) or str(poll))
            print(f"[Kyuubi ERROR] {task_name} 执行失败: {err}", flush=True)
            raise RuntimeError(err)

        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        print(f"[Kyuubi] 查询完成，返回 {len(rows)} 行", flush=True)
        conn.close()
        return rows
