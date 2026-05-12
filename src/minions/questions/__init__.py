"""Question Record subsystem — inter-agent escalation channel.

See ``minions/models/question.py`` for the model. Stores follow the same
dual-backend pattern as Decision / engineer-run stores.
"""

from minions.questions.service import answer_question, escalate_question, submit_question
from minions.questions.store import QuestionStore
from minions.questions.store_factory import QuestionStoreLike, make_question_store

__all__ = [
    "QuestionStore",
    "QuestionStoreLike",
    "answer_question",
    "escalate_question",
    "make_question_store",
    "submit_question",
]
