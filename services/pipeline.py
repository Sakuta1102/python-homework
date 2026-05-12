from concurrent.futures import ThreadPoolExecutor
from datetime import date
from dataclasses import dataclass

from config.projects import get_projects, ProjectConfig
from services.kyuubi import KyuubiClient
from services.feishu import FeishuClient

_MAX_PARALLEL = 4


@dataclass
class ProjectResult:
    name: str
    success: bool
    rows_written: int = 0
    error: str = ""


def run_pipeline(start_date: date, end_date: date) -> list[ProjectResult]:
    kyuubi = KyuubiClient()
    feishu = FeishuClient()
    projects = get_projects()  # 实时从飞书拉黑产关键词,组装最后 2 条 SQL
    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL) as pool:
        futures = [
            pool.submit(_run_single, kyuubi, feishu, p, start_date, end_date)
            for p in projects
        ]
        return [f.result() for f in futures]


def _run_single(
    kyuubi: KyuubiClient,
    feishu: FeishuClient,
    project: ProjectConfig,
    start_date: date,
    end_date: date,
) -> ProjectResult:
    try:
        if not project.sql_template:
            raise ValueError("sql_template 不能为空")

        sql = project.sql_template.format(
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        print(f"[Pipeline] {project.name} 日期范围: {start_date.isoformat()} ~ {end_date.isoformat()}", flush=True)

        rows = kyuubi.run_query(sql=sql, task_name=project.name)

        if project.feishu_spreadsheet_token and project.feishu_sheet_id:
            feishu.write_rows(
                spreadsheet_token=project.feishu_spreadsheet_token,
                sheet_id=project.feishu_sheet_id,
                rows=rows,
                start_row=project.feishu_start_row,
            )
        else:
            feishu.write_to_wiki(
                wiki_token=project.feishu_wiki_token,
                rows=rows,
                sheet_title=project.feishu_sheet_title,
                start_row=project.feishu_start_row,
            )

        return ProjectResult(name=project.name, success=True, rows_written=len(rows))

    except Exception as exc:
        import traceback
        print(f"[Pipeline ERROR] {project.name}: {exc}")
        traceback.print_exc()
        return ProjectResult(name=project.name, success=False, error=str(exc))
