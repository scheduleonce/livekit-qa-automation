from __future__ import annotations

import argparse
import html
import xml.etree.ElementTree as ET
from pathlib import Path


def escape(value: str | None) -> str:
    return html.escape(value or "", quote=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("xml_file", type=Path)
    parser.add_argument("html_file", type=Path)
    parser.add_argument("--environment", default="")
    args = parser.parse_args()

    root = ET.parse(args.xml_file).getroot()
    cases = root.findall(".//testcase")

    passed = failed = skipped = 0
    rows = []

    for case in cases:
        failure = case.find("failure")
        error = case.find("error")
        skip = case.find("skipped")

        if failure is not None or error is not None:
            status = "Failed"
            failed += 1
        elif skip is not None:
            status = "Skipped"
            skipped += 1
        else:
            status = "Passed"
            passed += 1

        name = case.get("name", "")
        classname = case.get("classname", "")
        duration = case.get("time", "0")

        output = case.findtext("system-out") or ""
        stderr = case.findtext("system-err") or ""

        issue = failure if failure is not None else error
        details = []
        if issue is not None:
            details.append(
                f"<h4>Failure</h4><pre>{escape(issue.get('message'))}\n"
                f"{escape(issue.text)}</pre>"
            )
        if output:
            details.append(f"<h4>Test output</h4><pre>{escape(output)}</pre>")
        if stderr:
            details.append(f"<h4>Standard error</h4><pre>{escape(stderr)}</pre>")
        if skip is not None and skip.text:
            details.append(f"<h4>Skip reason</h4><pre>{escape(skip.text)}</pre>")

        detail_html = (
            f"<details><summary>View logs and details</summary>"
            f"{''.join(details)}</details>"
            if details
            else "—"
        )

        rows.append(
            "<tr>"
            f"<td>{escape(status)}</td>"
            f"<td>{escape(classname)}<br><strong>{escape(name)}</strong></td>"
            f"<td>{escape(duration)} s</td>"
            f"<td>{detail_html}</td>"
            "</tr>"
        )

    total = len(cases)
    report = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LiveKit QA Test Report</title>
</head>
<body>
<h1>LiveKit QA Test Report</h1>
<p><strong>Environment:</strong> {escape(args.environment)}</p>
<p>
  <strong>Total:</strong> {total} &nbsp;
  <strong>Passed:</strong> {passed} &nbsp;
  <strong>Failed:</strong> {failed} &nbsp;
  <strong>Skipped:</strong> {skipped}
</p>
<table border="1" cellpadding="8" cellspacing="0">
<thead>
<tr>
  <th>Result</th>
  <th>Test</th>
  <th>Duration</th>
  <th>Logs and details</th>
</tr>
</thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
</body>
</html>
"""

    args.html_file.parent.mkdir(parents=True, exist_ok=True)
    args.html_file.write_text(report, encoding="utf-8")
    print(f"Static HTML report written: {args.html_file} ({total} tests)")


if __name__ == "__main__":
    main()