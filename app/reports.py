"""Validate structured coaching reports against the discipline they were made for.

Claude's JSON-schema output guarantees the shape of a report. It does not
guarantee that frame numbers point at frames that were sent, that findings
belong to the selected sport, or that drills are backed by a finding. Those
checks happen here.
"""
import json
import math
import re

from disciplines import CONFIDENCE, DISCIPLINES, EVIDENCE_STATUS, MAX_PRIORITIES, report_schema


class ReportError(Exception):
    """The model returned a report that cannot be used."""


class ModelRefused(ReportError):
    """The safety system declined to produce a report."""


def format_time(seconds):
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:04.1f}"


OVERCLAIM = re.compile(
    r"\b(power|powerful|speed|speedy|habit|habits|always|never|consistently|tends?\s+to|every\s+time)\b",
    re.IGNORECASE,
)


def is_overclaim(text):
    """Claims about habits, power, or speed cannot be established from sparse frames."""
    return bool(OVERCLAIM.search(text or ""))


def _text(value, field):
    if not isinstance(value, str):
        raise ReportError(f"{field} must be text.")
    return value.strip()


def _frame_refs(value, frame_count):
    if not isinstance(value, list):
        return []
    refs = {item for item in value if isinstance(item, int) and not isinstance(item, bool)}
    return sorted(ref for ref in refs if 1 <= ref <= frame_count)


def _confidence(value, refs):
    if value not in CONFIDENCE:
        raise ReportError("A finding has an unknown confidence level.")
    if value == "observed" and not refs:
        return "uncertain"
    return value


def validate_report(data, frame_times, discipline):
    if not isinstance(data, dict):
        raise ReportError("The report is not a JSON object.")
    schema = report_schema(discipline)
    missing = [key for key in schema["required"] if key not in data]
    if missing:
        raise ReportError(f"The report is missing: {', '.join(missing)}.")
    areas = DISCIPLINES[discipline]["areas"]
    count = len(frame_times)

    evidence_status = data["evidence_status"]
    if evidence_status not in EVIDENCE_STATUS:
        raise ReportError("The report has an unknown evidence status.")
    insufficient = evidence_status == "insufficient"

    observations = []
    for item in data["observations"]:
        description = _text(item.get("description", ""), "observation")
        if not description or is_overclaim(description):
            continue
        refs = _frame_refs(item.get("frames"), count)
        observations.append({
            "description": description,
            "frames": refs,
            "confidence": _confidence(item.get("confidence"), refs),
        })

    findings = []
    for item in data["technical_findings"]:
        if item.get("area") not in areas:
            raise ReportError(f"A technical finding is not a {DISCIPLINES[discipline]['label']} area.")
        finding = _text(item.get("finding", ""), "finding")
        if not finding or is_overclaim(finding):
            continue
        refs = _frame_refs(item.get("frames"), count)
        findings.append({
            "area": item["area"],
            "finding": finding,
            "fix": _text(item.get("fix", ""), "fix"),
            "frames": refs,
            "confidence": _confidence(item.get("confidence"), refs),
        })

    supported_areas = {f["area"] for f in findings if f["confidence"] in ("observed", "inferred")}

    drills = []
    for item in data["drills"]:
        if item.get("addresses") not in areas:
            raise ReportError(f"A drill is not a {DISCIPLINES[discipline]['label']} area.")
        if item["addresses"] not in supported_areas:
            continue
        drills.append({
            "name": _text(item.get("name", ""), "drill name"),
            "addresses": item["addresses"],
            "instructions": _text(item.get("instructions", ""), "instructions"),
            "objective": _text(item.get("objective", ""), "objective"),
            "reps_or_duration": _text(item.get("reps_or_duration", ""), "repetitions"),
        })

    priorities = []
    for item in data["top_priorities"][:MAX_PRIORITIES]:
        title = _text(item.get("title", ""), "priority title")
        reason = _text(item.get("reason", ""), "priority reason")
        if not title or is_overclaim(title) or is_overclaim(reason):
            continue
        priorities.append({
            "title": title,
            "reason": reason,
            "frames": _frame_refs(item.get("frames"), count),
        })

    if insufficient:
        findings, drills, priorities = [], [], []

    return {
        "discipline": discipline,
        "discipline_label": DISCIPLINES[discipline]["label"],
        "evidence_status": evidence_status,
        "evidence_note": _text(data["evidence_note"], "evidence note"),
        "summary": _text(data["summary"], "summary"),
        "sampling_caveat": _text(data["sampling_caveat"], "sampling caveat"),
        "frames": [
            {"number": i, "time_seconds": round(t, 2), "label": format_time(t)}
            for i, t in enumerate(frame_times, start=1)
        ],
        "observations": observations,
        "technical_findings": findings,
        "drills": drills,
        "top_priorities": priorities,
    }


def parse_report(text, frame_times, discipline):
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReportError("The report was not valid JSON.") from exc
    return validate_report(data, frame_times, discipline)


def validate_frame_times(raw, frame_count):
    if not isinstance(raw, list) or len(raw) != frame_count:
        raise ValueError("Send one time for each frame.")
    times = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ValueError("Frame times must be numbers.")
        if item < 0 or item > 86_400:
            raise ValueError("Frame times must be between 0 and 24 hours.")
        times.append(float(item))
    if times != sorted(times):
        raise ValueError("Frame times must be in order.")
    return times


def to_plain_text(report):
    labels = DISCIPLINES.get(report.get("discipline"), {}).get("areas", {})
    lines = [f"{report.get('discipline_label', 'Coaching')} report", report["summary"], ""]
    if report["evidence_status"] != "sufficient":
        lines.extend([f"Evidence: {report['evidence_status']}. {report['evidence_note']}", ""])
    if report["top_priorities"]:
        lines.append("Top priorities:")
        for i, item in enumerate(report["top_priorities"], start=1):
            lines.append(f"{i}. {item['title']}: {item['reason']}")
        lines.append("")
    if report["observations"]:
        lines.append("Observations:")
        for item in report["observations"]:
            lines.append(f"- [{item['confidence']}] {item['description']}")
        lines.append("")
    if report["technical_findings"]:
        lines.append("Technical review:")
        for item in report["technical_findings"]:
            area = labels.get(item["area"], item["area"])
            lines.append(f"- {area}: {item['finding']} Fix: {item['fix']}")
        lines.append("")
    if report["drills"]:
        lines.append("Corrective drills:")
        for item in report["drills"]:
            lines.append(f"- {item['name']} ({item['reps_or_duration']}): {item['instructions']} Objective: {item['objective']}")
        lines.append("")
    lines.append(f"Sampling caveat: {report['sampling_caveat']}")
    return "\n".join(lines)
