"""relay.schemas — Pydantic v2 I/O models.

Every public name matches the field names in frontend/src/types.ts exactly.
Import from the sub-modules directly or use the convenience re-exports below.
"""
from relay.schemas.common import ErrorDetail, ErrorResponse, build_event
from relay.schemas.documents import (
    DocumentListResponse,
    DocumentRecord,
    DocumentUploadResponse,
    document_to_schema,
)
from relay.schemas.cards import (
    Card,
    SessionCardsResponse,
    Source,
    card_to_schema,
    source_from_card_source,
)
from relay.schemas.sessions import (
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionResponse,
    SessionInfo,
    SessionListResponse,
    TranscriptResponse,
    Utterance,
    session_to_schema,
    utterance_to_schema,
)
from relay.schemas.leads import (
    BookLeadResponse,
    Lead,
    LeadListResponse,
    LeadQualifiers,
    RouteLeadResponse,
    lead_to_schema,
)
from relay.schemas.query import QueryRequest, QueryResponse
from relay.schemas.account import (
    LiveKitTokenResponse,
    Notification,
    NotificationListResponse,
    ReplyRequest,
    ReplyResponse,
    User,
    UserListResponse,
    notification_to_schema,
    user_to_schema,
)
from relay.schemas.ws import (
    CardDismissData,
    CardNewEvent,
    CardPinData,
    CardUpdateEvent,
    ClientEvent,
    ErrorEvent,
    ErrorEventData,
    LeadUpdateEvent,
    ModeSetData,
    QueryManualData,
    SessionStatusData,
    SessionStatusEvent,
    TranscriptFinalData,
    TranscriptFinalEvent,
    TranscriptPartialData,
    TranscriptPartialEvent,
)

__all__ = [
    # common
    "ErrorDetail",
    "ErrorResponse",
    "build_event",
    # documents
    "DocumentListResponse",
    "DocumentRecord",
    "DocumentUploadResponse",
    "document_to_schema",
    # cards
    "Card",
    "SessionCardsResponse",
    "Source",
    "card_to_schema",
    "source_from_card_source",
    # sessions
    "CreateSessionRequest",
    "CreateSessionResponse",
    "EndSessionResponse",
    "SessionInfo",
    "SessionListResponse",
    "TranscriptResponse",
    "Utterance",
    "session_to_schema",
    "utterance_to_schema",
    # leads
    "BookLeadResponse",
    "Lead",
    "LeadListResponse",
    "LeadQualifiers",
    "RouteLeadResponse",
    "lead_to_schema",
    # query
    "QueryRequest",
    "QueryResponse",
    # account
    "LiveKitTokenResponse",
    "Notification",
    "NotificationListResponse",
    "ReplyRequest",
    "ReplyResponse",
    "User",
    "UserListResponse",
    "notification_to_schema",
    "user_to_schema",
    # ws
    "CardDismissData",
    "CardNewEvent",
    "CardPinData",
    "CardUpdateEvent",
    "ClientEvent",
    "ErrorEvent",
    "ErrorEventData",
    "LeadUpdateEvent",
    "ModeSetData",
    "QueryManualData",
    "SessionStatusData",
    "SessionStatusEvent",
    "TranscriptFinalData",
    "TranscriptFinalEvent",
    "TranscriptPartialData",
    "TranscriptPartialEvent",
]
