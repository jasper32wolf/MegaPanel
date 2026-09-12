from enum import StrEnum


class UserRole(StrEnum):
    SUPERADMIN = "superadmin"
    TENANT_ADMIN = "tenant_admin"
    MANAGER = "manager"
    EDITOR = "editor"
    CLIENT = "client"
    VIEWER = "viewer"


class PublishState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class IndexState(StrEnum):
    NOINDEX = "noindex"
    QUEUED = "queued"
    INDEXED = "indexed"


class BuildStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class LeadStatus(StrEnum):
    NEW = "new"
    QUALIFIED = "qualified"
    SPAM = "spam"
    SENT = "sent"
    FAILED = "failed"
