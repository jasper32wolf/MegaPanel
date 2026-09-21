from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import Keyword
from app.schemas.common import KeywordImportRequest
from app.services.audit import append_audit
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

_WS = re.compile(r"\s+")
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 100_000
TEMPLATE = (
    "phrase,frequency,group,intent,city,priority\n"
    "ремонт стиральных машин,1200,ремонт стиральных машин,commercial,Москва,high\n"
)


def normalize_phrase(phrase: str) -> str:
    return _WS.sub(" ", phrase.strip().lower())


def parse_keyword_csv(
    raw: bytes, *, delimiter: str, phrase_column: str, column_map: dict[str, str]
) -> tuple[list[dict], list[dict]]:
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("File exceeds 10 MB")
    if delimiter not in {",", ";", "\t"}:
        raise ValueError("Unsupported CSV delimiter")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must be UTF-8 encoded") from exc

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames or phrase_column not in reader.fieldnames:
        raise ValueError(f"Column '{phrase_column}' is required")

    rows: list[dict] = []
    errors: list[dict] = []
    for line, source in enumerate(reader, start=2):
        if line > MAX_IMPORT_ROWS + 1:
            raise ValueError(f"CSV exceeds {MAX_IMPORT_ROWS} rows")
        phrase = (source.get(phrase_column) or "").strip()
        if not phrase:
            if len(errors) < 100:
                errors.append({"line": line, "error": "Empty phrase"})
            continue
        rows.append(
            {
                "phrase": phrase,
                "category": (source.get(column_map["group"]) or "").strip()
                if column_map["group"]
                else None,
                "meta": {
                    key: (source.get(column) or "").strip()
                    for key, column in column_map.items()
                    if key != "group" and column and (source.get(column) or "").strip()
                },
            }
        )
    return rows, errors


async def save_keywords(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    items: Iterable[dict],
) -> tuple[int, int]:
    existing = set(
        (await db.execute(select(Keyword.normalized).where(Keyword.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    created = 0
    skipped = 0
    for item in items:
        phrase = str(item["phrase"])
        normalized = normalize_phrase(phrase)
        if not normalized or normalized in existing:
            skipped += 1
            continue
        existing.add(normalized)
        db.add(
            Keyword(
                tenant_id=tenant_id,
                phrase=phrase.strip(),
                normalized=normalized,
                category=item.get("category"),
                meta=item.get("meta") or {},
            )
        )
        created += 1
    return created, skipped


def operator_scope(auth: AuthContext) -> UUID:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Operator scope required")
    return auth.tenant_id


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_keywords(
    body: KeywordImportRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tenant_id = operator_scope(auth)
    created, skipped = await save_keywords(
        db,
        tenant_id=tenant_id,
        items=({"phrase": item.phrase, "category": item.category} for item in body.items),
    )
    await append_audit(
        db,
        action="keywords.import_json",
        payload={"created": created, "skipped": skipped},
        tenant_id=tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"created": created, "skipped": skipped}


@router.get("/template.csv", response_class=PlainTextResponse)
async def keyword_template(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
) -> PlainTextResponse:
    return PlainTextResponse(
        TEMPLATE,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=site-panel-keywords-template.csv"},
    )


@router.post("/import-csv", status_code=status.HTTP_201_CREATED)
async def import_keywords_csv(
    file: UploadFile = File(...),
    phrase_column: str = Form("phrase"),
    group_column: str = Form("group"),
    frequency_column: str = Form("frequency"),
    intent_column: str = Form("intent"),
    city_column: str = Form("city"),
    priority_column: str = Form("priority"),
    delimiter: str = Form(","),
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tenant_id = operator_scope(auth)
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    try:
        items, errors = parse_keyword_csv(
            raw,
            delimiter=delimiter,
            phrase_column=phrase_column,
            column_map={
                "group": group_column,
                "frequency": frequency_column,
                "intent": intent_column,
                "city": city_column,
                "priority": priority_column,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created, skipped = await save_keywords(db, tenant_id=tenant_id, items=items)
    await append_audit(
        db,
        action="keywords.import_csv",
        payload={"created": created, "skipped": skipped, "invalid_rows": len(errors)},
        tenant_id=tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"created": created, "skipped": skipped, "invalid_rows": len(errors), "errors": errors}


@router.get("")
async def list_keywords(
    q: str | None = Query(None, min_length=1, max_length=200),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tenant_id = operator_scope(auth)
    statement = (
        select(Keyword).where(Keyword.tenant_id == tenant_id).order_by(Keyword.created_at.desc())
    )
    if q:
        statement = statement.where(Keyword.normalized.ilike(f"%{normalize_phrase(q)}%"))
    rows = list((await db.execute(statement.offset(offset).limit(limit))).scalars().all())
    return {
        "items": [
            {
                "id": str(row.id),
                "phrase": row.phrase,
                "category": row.category,
                "meta": row.meta or {},
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "offset": offset,
        "limit": limit,
    }
