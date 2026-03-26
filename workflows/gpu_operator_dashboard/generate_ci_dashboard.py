import json
import argparse

from typing import Dict, List, Any, Tuple
from datetime import datetime, timezone

from workflows.common.utils import logger
from workflows.common.templates import load_template
from workflows.gpu_operator_dashboard.fetch_ci_data import (
    OCP_FULL_VERSION, GPU_OPERATOR_VERSION, STATUS_ABORTED)


def version_sort_key(version_str: str) -> Tuple[int, ...]:
    """Convert a version string like '4.18', '4.9', '1.3.x' into a tuple of ints for proper sorting.

    Non-numeric segments (like 'x') are treated as 0.
    """
    parts = []
    for part in version_str.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def generate_test_matrix(ocp_data: Dict[str, Dict[str, Any]]) -> str:
    """
    Build the final HTML report by:
      1. Reading the header template,
      2. Generating the table blocks for each OCP version,
      3. Reading the footer template and injecting the last-updated time.
    """
    header_template = load_template("header.html")
    html_content = header_template
    main_table_template = load_template("main_table.html")
    sorted_ocp_keys = sorted(ocp_data.keys(), key=version_sort_key, reverse=True)
    html_content += build_toc(sorted_ocp_keys)

    for ocp_key in sorted_ocp_keys:
        notes = ocp_data[ocp_key].get("notes", [])
        bundle_results = ocp_data[ocp_key].get("bundle_tests", [])
        release_results = ocp_data[ocp_key].get("release_tests", [])

        regular_results = []
        for r in release_results:
            if r.get("test_status") != STATUS_ABORTED:
                regular_results.append(r)
        notes_html = build_notes(notes)
        table_rows_html = build_catalog_table_rows(regular_results)
        bundle_info_html = build_bundle_info(bundle_results)
        table_block = main_table_template
        table_block = table_block.replace("{ocp_key}", ocp_key)
        table_block = table_block.replace("{table_rows}", table_rows_html)
        table_block = table_block.replace("{bundle_info}", bundle_info_html)
        table_block = table_block.replace("{notes}", notes_html)
        html_content += table_block

    footer_template = load_template("footer.html")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    footer_template = footer_template.replace("{LAST_UPDATED}", now_str)
    html_content += footer_template
    return html_content


def build_catalog_table_rows(regular_results: List[Dict[str, Any]]) -> str:
    """
    Build the <tr> rows for the table, grouped by the full OCP version.

    For each OCP version group, determine the final status for each GPU version combination:
    - If there are any successful results for a combination, mark as successful
    - If there are only failed results for a combination, mark as failed
    """
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
            has_success = any(r["test_status"] == "SUCCESS" for r in gpu_results)

            if has_success:
                successful_results = [r for r in gpu_results if r["test_status"] == "SUCCESS"]
                chosen = max(successful_results, key=lambda r: int(r["job_timestamp"]))
                final_result = {**chosen, "final_status": "SUCCESS"}
            else:
                latest_result = max(gpu_results, key=lambda r: int(r["job_timestamp"]))
                final_result = {**latest_result, "final_status": "FAILURE"}

            final_results[gpu] = final_result

        sorted_results = sorted(
            final_results.values(),
            key=lambda r: version_sort_key(r[GPU_OPERATOR_VERSION]),
            reverse=True
        )

        gpu_links = []
        for r in sorted_results:
            if r["final_status"] == "SUCCESS":
                link = f'<a href="{r["prow_job_url"]}" target="_blank" class="success-link">{r[GPU_OPERATOR_VERSION]}</a>'
            else:
                link = f'<a href="{r["prow_job_url"]}" target="_blank" class="failed-link">{r[GPU_OPERATOR_VERSION]} (Failed)</a>'
            gpu_links.append(link)

        gpu_links_html = ", ".join(gpu_links)

        rows_html += f"""
        <tr>
          <td class="version-cell">{ocp_full}</td>
          <td>{gpu_links_html}</td>
        </tr>
        """

    return rows_html


def build_notes(notes: List[str]) -> str:
    if not notes:
        return ""

    items = "\n".join(f'<li class="note-item">{n}</li>' for n in notes)
    return f"""
  <div class="section-label">Notes</div>
  <div class="note-items">
    <ul>
      {items}
    </ul>
  </div>
    """


def build_toc(ocp_keys: List[str]) -> str:
    toc_links = ", ".join(
        f'<a href="#ocp-{ocp_version}">{ocp_version}</a>' for ocp_version in ocp_keys)
    return f"""
<div class="toc">
    <div class="ocp-version-header">OpenShift Versions</div>
    {toc_links}
</div>
    """


def build_bundle_info(bundle_results: List[Dict[str, Any]]) -> str:
    """
    Build a small HTML snippet that displays info about GPU bundle statuses
    (shown in a 'history-bar' with colored squares).
    """
    if not bundle_results:
        return ""
    sorted_bundles = sorted(
        bundle_results, key=lambda r: int(r["job_timestamp"]), reverse=True)
    leftmost_bundle = sorted_bundles[0]
    last_bundle_date = datetime.fromtimestamp(int(
        leftmost_bundle["job_timestamp"]), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    bundle_html = f"""
  <div class="section-label">
    <strong>From main branch (OLM bundle)</strong>
  </div>
  <div class="history-bar-inner history-bar-outer">
    <div style="margin-top: 5px;">
      <strong>Last Bundle Job Date:</strong> {last_bundle_date}
    </div>
    """
    for bundle in sorted_bundles:
        status = bundle.get("test_status", "Unknown").upper()
        if status == "SUCCESS":
            status_class = "history-success"
        elif status == "FAILURE":
            status_class = "history-failure"
        else:
            status_class = "history-aborted"
        bundle_timestamp = datetime.fromtimestamp(
            int(bundle["job_timestamp"]), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        bundle_html += f"""
    <div class='history-square {status_class}'
         onclick='window.open("{bundle["prow_job_url"]}", "_blank")'>
         <span class="history-square-tooltip">
          Status: {status} | Timestamp: {bundle_timestamp}
         </span>
    </div>
        """
    bundle_html += "</div>"
    return bundle_html


def main():
    parser = argparse.ArgumentParser(description="Test Matrix Utility")
    parser.add_argument("--dashboard_html_filepath", required=True,
                        help="Path to to html file for the dashboard")
    parser.add_argument("--dashboard_data_filepath", required=True,
                        help="Path to the file containing the versions for the dashboard")
    args = parser.parse_args()
    with open(args.dashboard_data_filepath, "r") as f:
        ocp_data = json.load(f)
    logger.info(
        f"Loaded JSON data with keys: {list(ocp_data.keys())} from {args.dashboard_data_filepath}")

    html_content = generate_test_matrix(ocp_data)

    with open(args.dashboard_html_filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
        logger.info(
            f"Matrix dashboard generated: {args.dashboard_html_filepath}")


if __name__ == "__main__":
    main()
