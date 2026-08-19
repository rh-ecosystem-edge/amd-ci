import html
import json
import argparse

from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone

from workflows.common.utils import logger
from workflows.common.templates import load_template
from workflows.common.broken_versions import find_broken_entry, load_broken_versions, notes_for_ocp_key
from workflows.gpu_operator_dashboard.fetch_ci_data import (
    OCP_FULL_VERSION, GPU_OPERATOR_VERSION, STATUS_ABORTED, STATUS_SUCCESS)


def version_sort_key(version_str: str) -> Tuple[int, ...]:
    parts = []
    for part in version_str.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _success_rate(success: int, total: int) -> int:
    return round(100 * success / total) if total > 0 else 0


def _compute_stats(
    ocp_data: Dict[str, Any],
    broken_entries: List[Dict[str, Any]],
    sorted_ocp_keys: List[str],
) -> Tuple[Dict[str, Dict[str, int]], int, int]:
    """Return per-OCP-key success/total counts plus overall totals."""
    ocp_stats: Dict[str, Dict[str, int]] = {}
    total_combos = 0
    total_success = 0

    for ocp_key in sorted_ocp_keys:
        data = ocp_data[ocp_key]
        release_tests = data.get("release_tests", [])
        filtered = [
            r for r in release_tests
            if r.get("test_status") != STATUS_ABORTED
            and not find_broken_entry(r.get(OCP_FULL_VERSION), r.get(GPU_OPERATOR_VERSION), broken_entries)
        ]

        combos: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for r in filtered:
            key = (r[OCP_FULL_VERSION], r[GPU_OPERATOR_VERSION])
            combos.setdefault(key, []).append(r)

        n_combos = len(combos)
        n_success = sum(
            1 for results in combos.values()
            if any(r["test_status"] == STATUS_SUCCESS for r in results)
        )

        ocp_stats[ocp_key] = {"total": n_combos, "success": n_success}
        total_combos += n_combos
        total_success += n_success

    return ocp_stats, total_combos, total_success


def build_summary_stats_html(total_combos: int, total_success: int, n_ocp: int) -> str:
    rate = _success_rate(total_success, total_combos)
    rate_class = "rate-high" if rate >= 75 else "rate-mid" if rate >= 50 else "rate-low"
    return f"""  <div class="summary-stats">
    <div class="stat-card">
      <span class="stat-number">{total_combos}</span>
      <span class="stat-label">Combinations Tested</span>
    </div>
    <div class="stat-card">
      <span class="stat-number {rate_class}">{rate}%</span>
      <span class="stat-label">Overall Success Rate</span>
    </div>
    <div class="stat-card">
      <span class="stat-number">{n_ocp}</span>
      <span class="stat-label">OCP Versions</span>
    </div>
  </div>
  <div class="chart-section">
    <div class="chart-title">Success Rate by OCP Version</div>
    <div class="chart-wrapper">
      <canvas id="successChart"></canvas>
    </div>
  </div>
"""


def build_chart_script(
    ocp_stats: Dict[str, Dict[str, int]],
    sorted_ocp_keys: List[str],
) -> str:
    keys_asc = list(reversed(sorted_ocp_keys))
    labels = json.dumps(keys_asc)
    rates = json.dumps([_success_rate(ocp_stats[k]["success"], ocp_stats[k]["total"]) for k in keys_asc])
    counts = json.dumps([f"{ocp_stats[k]['success']}/{ocp_stats[k]['total']}" for k in keys_asc])

    return f"""<script>
(function() {{
  var labels = {labels};
  var rates = {rates};
  var counts = {counts};
  var colors = rates.map(function(v) {{
    return v >= 75 ? '#22c55e' : v >= 50 ? '#f59e0b' : '#ef4444';
  }});
  new Chart(document.getElementById('successChart'), {{
    type: 'bar',
    data: {{
      labels: labels,
      datasets: [{{
        data: rates,
        backgroundColor: colors,
        borderRadius: 4,
        barThickness: 20,
      }}]
    }},
    options: {{
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: function(ctx) {{
              return ' ' + rates[ctx.dataIndex] + '% (' + counts[ctx.dataIndex] + ' passed)';
            }}
          }}
        }}
      }},
      scales: {{
        x: {{
          min: 0,
          max: 100,
          ticks: {{ callback: function(v) {{ return v + '%'; }}, maxTicksLimit: 6 }},
          grid: {{ color: '#e2e8f0' }},
        }},
        y: {{ grid: {{ display: false }} }},
      }}
    }}
  }});
}})();
</script>
"""


def build_success_badge(success: int, total: int) -> str:
    rate = _success_rate(success, total)
    label = f"{success}/{total} passed"
    if rate >= 75:
        badge_class = "badge-success"
    elif rate >= 50:
        badge_class = "badge-mixed"
    else:
        badge_class = "badge-failure"
    return f'<span class="badge {badge_class}">{label}</span>'


def _build_test_details_html(test_details: Optional[List[Dict[str, str]]]) -> str:
    if not test_details:
        return '<span class="no-details">—</span>'

    groups: Dict[str, List[Dict[str, str]]] = {}
    for test in test_details:
        cls = test.get("class", "Tests")
        groups.setdefault(cls, []).append(test)

    n_passed = sum(1 for t in test_details if t.get("status") == "PASSED")
    n_total = len(test_details)

    groups_html = ""
    for cls, tests in groups.items():
        cls_display = cls.removeprefix("Test")
        tests_html = ""
        for t in tests:
            name = t.get("name", "").removeprefix("test_").replace("_", " ")
            status = t.get("status", "")
            css = {"PASSED": "passed", "FAILED": "failed", "SKIPPED": "skipped", "ERROR": "failed"}.get(status, "")
            icon = "✓" if status == "PASSED" else "✗" if status in ("FAILED", "ERROR") else "○"
            tests_html += f'\n          <div class="test-item {css}">{icon} {html.escape(name)}</div>'
        groups_html += f"""
      <div class="test-group">
        <div class="test-group-name">{html.escape(cls_display)}</div>{tests_html}
      </div>"""

    return f"""<details class="test-details">
      <summary>{n_passed}/{n_total} passed &#9656;</summary>
      <div class="test-details-body">{groups_html}
      </div>
    </details>"""


def build_catalog_table_rows(regular_results: List[Dict[str, Any]]) -> str:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for result in regular_results:
        ocp_full = result[OCP_FULL_VERSION]
        grouped.setdefault(ocp_full, []).append(result)

    rows_html = ""
    for ocp_full in sorted(grouped.keys(), key=version_sort_key, reverse=True):
        rows = grouped[ocp_full]

        gpu_groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            gpu = row[GPU_OPERATOR_VERSION]
            gpu_groups.setdefault(gpu, []).append(row)

        final_results: Dict[str, Dict[str, Any]] = {}
        for gpu, gpu_results in gpu_groups.items():
            has_success = any(r["test_status"] == STATUS_SUCCESS for r in gpu_results)
            if has_success:
                successful = [r for r in gpu_results if r["test_status"] == STATUS_SUCCESS]
                chosen = max(successful, key=lambda r: int(r["job_timestamp"]))
            else:
                chosen = max(gpu_results, key=lambda r: int(r["job_timestamp"]))
            final_results[gpu] = {**chosen, "final_status": STATUS_SUCCESS if has_success else "FAILURE"}

        sorted_results = sorted(
            final_results.values(),
            key=lambda r: version_sort_key(r[GPU_OPERATOR_VERSION]),
            reverse=True,
        )

        for idx, r in enumerate(sorted_results):
            gpu_version = html.escape(r[GPU_OPERATOR_VERSION])
            prow_url = html.escape(r["prow_job_url"])
            final_status = r["final_status"]
            test_details = r.get("test_details")

            status_html = (
                '<span class="badge badge-success">&#10003; PASSED</span>'
                if final_status == STATUS_SUCCESS
                else '<span class="badge badge-failure">&#10007; FAILED</span>'
            )
            gpu_link = f'<a href="{prow_url}" target="_blank">{gpu_version}</a>'
            details_html = _build_test_details_html(test_details)
            ocp_cell = (
                f'<td class="td-ocp">{html.escape(ocp_full)}</td>'
                if idx == 0
                else '<td class="td-ocp td-ocp-cont"></td>'
            )

            rows_html += f"""        <tr>
          {ocp_cell}
          <td class="td-gpu">{gpu_link}</td>
          <td>{status_html}</td>
          <td>{details_html}</td>
        </tr>\n"""

    return rows_html


def build_notes(notes: List[str]) -> str:
    if not notes:
        return ""
    items = "\n".join(f'<li>{html.escape(n)}</li>' for n in notes)
    return f'<div class="notes-section"><ul>{items}</ul></div>\n'


def build_toc(ocp_keys: List[str]) -> str:
    links = "\n    ".join(f'<a href="#ocp-{v}">{v}</a>' for v in ocp_keys)
    return f"""  <div class="toc">
    <span class="toc-label">Jump to OCP version:</span>
    {links}
  </div>\n"""


def generate_test_matrix(
    ocp_data: Dict[str, Dict[str, Any]],
    broken_entries: Optional[List[Dict[str, Any]]] = None,
) -> str:
    broken_entries = broken_entries or []
    header_template = load_template("header.html")
    main_table_template = load_template("main_table.html")
    footer_template = load_template("footer.html")

    sorted_ocp_keys = sorted(ocp_data.keys(), key=version_sort_key, reverse=True)
    ocp_stats, total_combos, total_success = _compute_stats(ocp_data, broken_entries, sorted_ocp_keys)

    html_content = header_template
    html_content += build_summary_stats_html(total_combos, total_success, len(sorted_ocp_keys))
    html_content += build_toc(sorted_ocp_keys)

    for ocp_key in sorted_ocp_keys:
        notes = list(ocp_data[ocp_key].get("notes", [])) + notes_for_ocp_key(ocp_key, broken_entries)
        release_tests = ocp_data[ocp_key].get("release_tests", [])

        regular_results = [
            r for r in release_tests
            if r.get("test_status") != STATUS_ABORTED
            and not find_broken_entry(r.get(OCP_FULL_VERSION), r.get(GPU_OPERATOR_VERSION), broken_entries)
        ]

        notes_html = build_notes(notes)
        table_rows_html = build_catalog_table_rows(regular_results)
        stats = ocp_stats.get(ocp_key, {"total": 0, "success": 0})
        success_badge_html = build_success_badge(stats["success"], stats["total"])

        table_block = main_table_template
        table_block = table_block.replace("{ocp_key}", ocp_key)
        table_block = table_block.replace("{table_rows}", table_rows_html)
        table_block = table_block.replace("{notes}", notes_html)
        table_block = table_block.replace("{success_badge}", success_badge_html)
        html_content += table_block

    chart_script = build_chart_script(ocp_stats, sorted_ocp_keys)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    footer_out = footer_template.replace("{CHART_SCRIPT}", chart_script)
    footer_out = footer_out.replace("{LAST_UPDATED}", now_str)
    html_content += footer_out

    return html_content


def main():
    parser = argparse.ArgumentParser(description="Test Matrix Utility")
    parser.add_argument("--dashboard_html_filepath", required=True,
                        help="Path to html file for the dashboard")
    parser.add_argument("--dashboard_data_filepath", required=True,
                        help="Path to the file containing the versions for the dashboard")
    parser.add_argument("--broken_versions_filepath",
                        default="workflows/gpu_operator_versions/broken_versions.json",
                        help="Path to the file listing OCP/GPU operator version combinations "
                             "known to be broken; matching results are hidden and a note is added")
    args = parser.parse_args()

    with open(args.dashboard_data_filepath, "r") as f:
        ocp_data = json.load(f)
    logger.info(
        f"Loaded JSON data with keys: {list(ocp_data.keys())} from {args.dashboard_data_filepath}")

    broken_entries = load_broken_versions(args.broken_versions_filepath)
    html_content = generate_test_matrix(ocp_data, broken_entries)

    with open(args.dashboard_html_filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
    logger.info(f"Matrix dashboard generated: {args.dashboard_html_filepath}")


if __name__ == "__main__":
    main()
