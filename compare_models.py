from __future__ import annotations

import argparse
import json
import sys
import time

from app.azure_clients import chat_completion, hybrid_retrieve
from app.config import get_settings
from app.database import format_history_for_prompt
from app.rag import build_messages


DEFAULT_QUESTIONS = [
    "How many days of PTO do employees get?",
    "What is the deductible on the Bronze plan?",
    "What should I do if I lose my laptop?",
    "How do I enroll in benefits as a new employee?",
    "What should I do after witnessing harassment?",
    "Can I use public Wi-Fi for company work?",
    "What expenses require receipts for reimbursement?",
    "What EAP services are available to employees?",
]


def load_questions(path: str | None) -> list[str]:
    if not path:
        return DEFAULT_QUESTIONS
    with open(path, "r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, list) or not all(isinstance(item, str) for item in loaded):
        raise ValueError("Question file must contain a JSON array of strings.")
    return loaded


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Compare Meridian RAG answers across two deployments.")
    parser.add_argument("--questions", help="Optional JSON file containing 8 test question strings.")
    args = parser.parse_args()

    settings = get_settings()
    deployments = [settings.generation_deployment_primary, settings.generation_deployment_comparison]
    questions = load_questions(args.questions)
    history = format_history_for_prompt([])

    for question_index, question in enumerate(questions, start=1):
        chunks = hybrid_retrieve(settings, question, top=5)
        messages = build_messages(question, history, chunks)
        print(f"\nQuestion {question_index}: {question}")
        for deployment in deployments:
            started = time.perf_counter()
            latency_ms = int((time.perf_counter() - started) * 1000)
            print(f"\nModel: {deployment}")
            try:
                answer, usage = chat_completion(settings, messages, deployment=deployment)
                latency_ms = int((time.perf_counter() - started) * 1000)
                print(f"Latency: {latency_ms} ms")
                print(
                    "Tokens: "
                    f"prompt={usage.get('prompt_tokens', 'n/a')} "
                    f"completion={usage.get('completion_tokens', 'n/a')} "
                    f"total={usage.get('total_tokens', 'n/a')}"
                )
                print(f"Response: {answer}")
            except Exception as exc:  # noqa: BLE001 - comparison should report per-model failures
                latency_ms = int((time.perf_counter() - started) * 1000)
                print(f"Latency: {latency_ms} ms")
                print("Tokens: prompt=n/a completion=n/a total=n/a")
                print(f"Error: {exc}")


if __name__ == "__main__":
    main()
