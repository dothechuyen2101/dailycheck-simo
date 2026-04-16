"""
Report Generator – Tạo báo cáo HTML gửi email mỗi sáng.
"""
from datetime import datetime
from typing import List
from src.logger import CheckResult, RunSummary


STATUS_COLOR = {
    "OK": "#28a745",
    "WARNING": "#ffc107",
    "CRITICAL": "#dc3545",
    "ERROR": "#6c757d",
}

STATUS_LABEL = {
    "OK": "✅ OK",
    "WARNING": "⚠️ CẢNH BÁO",
    "CRITICAL": "🔴 LỖI NGHIÊM TRỌNG",
    "ERROR": "❌ LỖI",
}


def generate_html_report(summary: RunSummary, results: List[CheckResult]) -> str:
    """Tạo báo cáo HTML đầy đủ."""
    date_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    overall_color = STATUS_COLOR.get(summary.overall_status, "#6c757d")
    overall_label = STATUS_LABEL.get(summary.overall_status, summary.overall_status)

    # Nhóm kết quả theo system
    systems: dict[str, list] = {}
    for r in results:
        systems.setdefault(r.system, []).append(r)

    rows_html = ""
    for system, sys_results in systems.items():
        rows_html += f"""
        <tr style="background:#f8f9fa;">
            <td colspan="5" style="padding:8px 12px;font-weight:bold;font-size:14px;
                border-top:2px solid #dee2e6;">
                🖥️ {system.upper()}
            </td>
        </tr>"""
        for r in sys_results:
            color = STATUS_COLOR.get(r.status, "#6c757d")
            label = STATUS_LABEL.get(r.status, r.status)
            rows_html += f"""
        <tr>
            <td style="padding:6px 12px;color:#555;">{r.host}</td>
            <td style="padding:6px 12px;">{r.check_name}</td>
            <td style="padding:6px 12px;font-weight:bold;color:{color};">{label}</td>
            <td style="padding:6px 12px;font-size:12px;">{r.message}</td>
            <td style="padding:6px 12px;font-size:11px;color:#888;">{r.checked_at[11:19]}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SIMO Daily Check Report – {date_str}</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; margin:0; padding:0; background:#f0f2f5; }}
  .container {{ max-width:900px; margin:20px auto; background:#fff;
                border-radius:8px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,.1); }}
  .header {{ background:{overall_color}; color:#fff; padding:24px 32px; }}
  .header h1 {{ margin:0; font-size:22px; }}
  .header p  {{ margin:4px 0 0; opacity:.85; font-size:14px; }}
  .summary {{ display:flex; padding:16px 32px; background:#fafafa;
              border-bottom:1px solid #e9ecef; gap:24px; flex-wrap:wrap; }}
  .stat {{ text-align:center; }}
  .stat .num {{ font-size:28px; font-weight:bold; }}
  .stat .lbl {{ font-size:12px; color:#666; }}
  .ok-num {{ color:#28a745; }} .warn-num {{ color:#ffc107; }}
  .crit-num {{ color:#dc3545; }} .err-num  {{ color:#6c757d; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ background:#343a40; color:#fff; padding:10px 12px; text-align:left; }}
  tr:nth-child(even) {{ background:#f8f9fa; }}
  .footer {{ padding:16px 32px; font-size:11px; color:#888;
             border-top:1px solid #e9ecef; text-align:center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📊 SIMO Daily Check Report</h1>
    <p>Ngày: {date_str} &nbsp;|&nbsp; Run ID: {summary.run_id} &nbsp;|&nbsp;
       Trạng thái tổng thể: <strong>{overall_label}</strong></p>
  </div>

  <div class="summary">
    <div class="stat"><div class="num">{summary.total_checks}</div><div class="lbl">Tổng kiểm tra</div></div>
    <div class="stat"><div class="num ok-num">{summary.ok_count}</div><div class="lbl">Bình thường</div></div>
    <div class="stat"><div class="num warn-num">{summary.warning_count}</div><div class="lbl">Cảnh báo</div></div>
    <div class="stat"><div class="num crit-num">{summary.critical_count}</div><div class="lbl">Lỗi nghiêm trọng</div></div>
    <div class="stat"><div class="num err-num">{summary.error_count}</div><div class="lbl">Lỗi kết nối</div></div>
    <div class="stat">
      <div class="num" style="font-size:14px;margin-top:4px;">{summary.started_at[11:16]} → {summary.finished_at[11:16] if summary.finished_at else '--:--'}</div>
      <div class="lbl">Thời gian thực hiện</div>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Host</th>
        <th>Nội dung kiểm tra</th>
        <th>Kết quả</th>
        <th>Chi tiết</th>
        <th>Thời gian</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>

  <div class="footer">
    Báo cáo được tạo tự động bởi <strong>SIMO Daily Check System v1.0</strong> &nbsp;|&nbsp;
    Phòng Phần mềm – SIMO &nbsp;|&nbsp; {date_str}
  </div>
</div>
</body>
</html>"""
    return html


def generate_subject(summary: RunSummary) -> str:
    label = STATUS_LABEL.get(summary.overall_status, summary.overall_status)
    date_str = datetime.now().strftime("%d/%m/%Y")
    return f"[SIMO DailyCheck] {label} – Báo cáo kiểm tra đầu ngày {date_str}"
