from __future__ import annotations


CONTEXT_REFERENCE_CUES = ("它", "这个", "上述", "前面", "该", "其", "那个")


def contextualize_retrieval_query(question: str, history: list[dict]) -> tuple[str, bool]:
    """Resolve a lightweight follow-up without loading the chat model.

    The previous LLM rewrite received only the current sentence, so it could
    not know what “它/这个” referred to. Reusing the last user question is
    deterministic, cheap, and keeps the original wording visible to retrieval.
    """
    if not history or not any(cue in question for cue in CONTEXT_REFERENCE_CUES):
        return question, False
    previous = next(
        (
            str(item.get("content") or "").strip()
            for item in reversed(history)
            if item.get("role") == "user" and str(item.get("content") or "").strip()
        ),
        "",
    )
    if not previous:
        return question, False
    return f"{previous}\n追问：{question}", True
