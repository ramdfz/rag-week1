from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.azure_clients import chat_completion
from app.config import get_settings
from app.database import get_chunk, init_chat_tables
from app.rag import answer_chat


JUDGE_MODEL = "gpt-5.5"
JUDGE_PROMPT = """You are an evaluator for a retrieval-augmented knowledge-base assistant.
Decide whether the assistant response accurately answers the user question using only the provided source passage(s).
Use the expected answer summary as a reference for what should be covered.
Return only JSON with this shape:
{"verdict":"yes|no|partial","reason":"short reason"}"""


@dataclass(frozen=True)
class EvalCase:
    question: str
    expected_doc: str | None
    expected_summary: str


CASES = [
    EvalCase(
        "How many days of PTO do employees get?",
        "pto-policy.docx",
        "The PTO policy is unlimited/flexible vacation rather than a fixed day count.",
    ),
    EvalCase(
        "How far in advance should I request a planned vacation?",
        "pto-policy.docx",
        "Planned absences should generally be requested 2-4 weeks in advance.",
    ),
    EvalCase(
        "Do I need a doctor's note if I am unexpectedly out sick for several days?",
        "pto-policy.docx",
        "For unplanned absences of 3 or more days due to injury or illness, provide a doctor's note.",
    ),
    EvalCase(
        "how do I report a coworker being rude to me",
        "anti-harassment-policy.docx",
        "The response should direct the employee to the harassment/reporting process or HR/supervisor channels.",
    ),
    EvalCase(
        "If I see workplace bullying or inappropriate comments, where should I report it?",
        "anti-harassment-policy.docx",
        "Report harassment or inappropriate conduct through the designated supervisor/HR reporting process.",
    ),
    EvalCase(
        "What does the attendance policy expect from employees?",
        "attendance-policy.docx",
        "Employees are expected to be present, on time, and regularly punctual.",
    ),
    EvalCase(
        "Why does Meridian have a dress code?",
        "dress-code-policy.docx",
        "The dress code helps employees present appropriately to clients, visitors, and other parties.",
    ),
    EvalCase(
        "What kinds of employee expenses can be reimbursed?",
        "expense-reimbursement-policy.docx",
        "Eligible work-related expenses incurred on behalf of Meridian are reimbursed in full.",
    ),
    EvalCase(
        "What is the deductible on the Bronze plan?",
        "cigna-connect-bronze-sbc-2026.pdf",
        "The Cigna Bronze plan deductible is $7,500 per person and $15,000 per family.",
    ),
    EvalCase(
        "What is the out-of-pocket limit on the Aetna Choice POS II plan?",
        "aetna-choice-pos-ii-sbc-2026.pdf",
        "The answer should cite the Aetna SBC out-of-pocket limit information.",
    ),
    EvalCase(
        "What does the Employee Assistance Program help employees with?",
        "bhs-eap-work-life-overview.pdf",
        "The EAP/work-life service helps employees with personal, work-life, and wellbeing support.",
    ),
    EvalCase(
        "What retirement plan document explains the 401k plan?",
        "fidelity-northern-light-health-retirement-spd.pdf",
        "The Fidelity/AHS Retirement Partnership 401(k) Summary Plan Description explains the plan.",
    ),
    EvalCase(
        "What is the out-of-pocket limit on the UHC Premier QDHP plan?",
        "uhc-premier-qdhp-sbc.pdf",
        "The answer should cite the UHC SBC out-of-pocket limit details.",
    ),
    EvalCase(
        "How long do new hires have to make benefits elections?",
        "benefits-enrollment-walkthrough.md",
        "New hires have 30 days from their start date to make initial benefits elections.",
    ),
    EvalCase(
        "What should I do if I lose my laptop?",
        "it-security-acceptable-use-policy.md",
        "The employee should report a lost or stolen company device promptly so IT can respond, including remote wipe if needed.",
    ),
    EvalCase(
        "what happens if I damage a company laptop",
        "it-security-acceptable-use-policy.md",
        "The response should use the company device/laptop policy and explain the employee should report damage or device issues.",
    ),
    EvalCase(
        "What counts as a reportable workplace incident?",
        "incident-reporting-procedure.md",
        "Reportable incidents include injuries, near misses, property damage over $500, client/patient/visitor incidents, and suspected data/security incidents.",
    ),
    EvalCase(
        "What data is classified as restricted?",
        "data-handling-confidentiality-guidelines.md",
        "Restricted data includes health information, Social Security numbers, and financial account details, requiring encryption and need-to-know access.",
    ),
    EvalCase(
        "When is VPN required?",
        "remote-access-vpn-policy.md",
        "VPN is required for internal systems from outside facilities, public/guest Wi-Fi company work, or systems with personal data.",
    ),
    EvalCase(
        "What is Meridian Health Partners stock price today?",
        None,
        "The assistant should refuse because the stock price is outside the knowledge base.",
    ),
]


def parse_verdict(raw: str) -> tuple[str, str]:
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            verdict = str(data.get("verdict", "")).lower()
            if verdict in {"yes", "no", "partial"}:
                return verdict, str(data.get("reason", "")).strip()
        except json.JSONDecodeError:
            pass
    lowered = raw.lower()
    for verdict in ("partial", "yes", "no"):
        if verdict in lowered:
            return verdict, raw.strip()
    return "no", raw.strip()


def source_text(settings, chunk_ids: list[str]) -> str:
    parts: list[str] = []
    for chunk_id in chunk_ids:
        row = get_chunk(settings.database_path, chunk_id)
        if row is None:
            continue
        parts.append(
            "\n".join(
                [
                    f"Document: {row['document']}",
                    f"Section: {row['section']}",
                    str(row["text"]),
                ]
            )
        )
    return "\n\n---\n\n".join(parts) if parts else "No source passage was cited."


def judge(settings, case: EvalCase, response: str, sources: str) -> tuple[str, str]:
    prompt = "\n\n".join(
        [
            f"Question: {case.question}",
            f"Expected answer summary: {case.expected_summary}",
            f"Source passage(s):\n{sources}",
            f"Assistant response:\n{response}",
        ]
    )
    raw, _usage = chat_completion(
        settings,
        [{"role": "system", "content": JUDGE_PROMPT}, {"role": "user", "content": prompt}],
        deployment=JUDGE_MODEL,
    )
    return parse_verdict(raw)


def md(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def main() -> None:
    settings = get_settings()
    init_chat_tables(settings.database_path)
    print(f"Judge model: {JUDGE_MODEL}")
    print("Judge prompt:")
    print(JUDGE_PROMPT)
    print()
    print("| # | Question | Expected doc | Actual doc(s) | Correct doc? | Judge | Reason |")
    print("| --- | --- | --- | --- | --- | --- | --- |")

    correct_docs = 0
    judge_positive = 0
    results = []
    for index, case in enumerate(CASES, start=1):
        response = answer_chat(settings, case.question, conversation_id=None)
        actual_docs = [citation.document for citation in response.citations]
        unique_actual_docs = list(dict.fromkeys(actual_docs))
        if case.expected_doc is None:
            doc_correct = not unique_actual_docs and "don't have that in the knowledge base" in response.response.lower()
        else:
            doc_correct = case.expected_doc in unique_actual_docs
        if doc_correct:
            correct_docs += 1

        sources = source_text(settings, [citation.chunk_id for citation in response.citations])
        verdict, reason = judge(settings, case, response.response, sources)
        if verdict in {"yes", "partial"}:
            judge_positive += 1
        results.append((case, unique_actual_docs, doc_correct, verdict, reason))
        print(
            f"| {index} | {md(case.question)} | {case.expected_doc or 'NONE'} | "
            f"{md(', '.join(unique_actual_docs) or 'NONE')} | {doc_correct} | {verdict} | {md(reason)} |"
        )

    total = len(CASES)
    print()
    print(f"Document citation accuracy: {correct_docs}/{total} ({correct_docs / total:.0%})")
    print(f"Judge yes-or-partial rate: {judge_positive}/{total} ({judge_positive / total:.0%})")


if __name__ == "__main__":
    main()
