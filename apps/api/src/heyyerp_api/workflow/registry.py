"""Document types that can go through approval.

A new type (indent, PO, bill, ...) is added by registering a DocumentType from its own module;
the engine never changes (BRD FR-WF-05).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from heyyerp_api.masters.common import Ctx


@dataclass(frozen=True)
class DocInfo:
    title: str
    amount: Decimal | None  # drives the value band; None for documents without an amount
    department_id: UUID | None  # drives DEPARTMENT_HEAD steps
    submittable: bool  # False when the document is not in a state that may be submitted
    reason: str = ""  # shown to the user when submittable is False


Loader = Callable[[Ctx, UUID], Awaitable[DocInfo | None]]
Finalizer = Callable[[Ctx, UUID, bool], Awaitable[None]]  # (ctx, document_id, approved)


@dataclass(frozen=True)
class DocumentType:
    code: str
    submit_permission: str
    load: Loader
    finalize: Finalizer


REGISTRY: dict[str, DocumentType] = {}

# The eight BRD document types plus vendor onboarding. Rules may be defined ahead of the module
# that registers the type, so administrators can prepare the matrix.
KNOWN_TYPES = frozenset(
    {"indent", "po", "bill", "scrap", "count", "leave", "kaizen", "review", "vendor", "bom"}
)


def register(doc_type: DocumentType) -> None:
    REGISTRY[doc_type.code] = doc_type
