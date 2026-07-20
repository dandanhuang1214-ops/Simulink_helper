from app.services.query_context import contextualize_retrieval_query
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, Conversation, Message
from app.services import conversations as conversation_service


def test_contextual_followup_reuses_last_user_question_without_llm() -> None:
    history = [
        {"role": "user", "content": "Stateflow 是什么？"},
        {"role": "assistant", "content": "Stateflow 是状态机建模工具。"},
    ]

    query, used = contextualize_retrieval_query("它如何与 Simulink 交互？", history)

    assert used is True
    assert "Stateflow 是什么" in query
    assert "它如何与 Simulink 交互" in query


def test_independent_question_is_not_contextualized() -> None:
    query, used = contextualize_retrieval_query(
        "固定步长求解器是什么？",
        [{"role": "user", "content": "Stateflow 是什么？"}],
    )

    assert used is False
    assert query == "固定步长求解器是什么？"


def test_api_restart_reconciles_stale_generating_messages(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(conversation_service, "SessionLocal", testing_session)

    with testing_session() as session:
        conversation = Conversation(title="restart test")
        session.add(conversation)
        session.flush()
        session.add_all([
            Message(conversation_id=conversation.id, role="assistant", status="generating", content="partial"),
            Message(conversation_id=conversation.id, role="assistant", status="completed", content="done"),
        ])
        session.commit()

    assert conversation_service.reconcile_interrupted_messages() == 1

    with testing_session() as session:
        rows = session.query(Message).order_by(Message.id).all()
        assert rows[0].status == "interrupted"
        assert rows[0].content == "partial"
        assert rows[1].status == "completed"
