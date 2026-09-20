#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap the single Site Panel operator")
    parser.add_argument("--email")
    parser.add_argument("--name", default="Site Panel Operator")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--tenant-slug", default="operator")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--reset-password", action="store_true")
    args = parser.parse_args()
    if args.interactive:
        if args.password_stdin:
            parser.error("--interactive cannot be combined with --password-stdin")
        args.email = input("Operator email: ").strip()
        name = input("Operator name [Site Panel Operator]: ").strip()
        if name:
            args.name = name
    elif not args.email:
        parser.error("--email is required unless --interactive is used")
    return args


def read_password(from_stdin: bool) -> str:
    password = (
        sys.stdin.readline().rstrip("\r\n")
        if from_stdin
        else getpass.getpass("Operator password: ")
    )
    if len(password) < 10:
        raise SystemExit("Password must contain at least 10 characters")
    return password


async def bootstrap(args: argparse.Namespace, password: str) -> None:
    from app.core.security import hash_password
    from app.db.session import open_db_session
    from app.models import Tenant, User
    from app.services.audit import append_audit
    from sqlalchemy import select

    email = args.email.strip().lower()
    if not email or "@" not in email:
        raise SystemExit("A valid operator email is required")
    slug = args.tenant_slug.strip().lower()
    if not slug or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in slug):
        raise SystemExit("Tenant slug must contain only lowercase letters, numbers, and hyphens")

    async with open_db_session() as db:
        users = list((await db.execute(select(User).limit(2))).scalars().all())
        if len(users) > 1:
            raise SystemExit(
                "Multiple user accounts exist. Resolve them before bootstrapping one operator."
            )

        user = users[0] if users else None
        if user is not None and user.email != email:
            raise SystemExit(
                "An operator account already exists. Use its email with "
                "--reset-password to reset it."
            )
        if user is not None and not args.reset_password:
            raise SystemExit(
                "Operator already exists. Re-run with --reset-password to replace its password."
            )

        action = "operator.bootstrap"
        if user is None:
            tenants = list((await db.execute(select(Tenant).limit(2))).scalars().all())
            if len(tenants) > 1:
                raise SystemExit(
                    "Multiple owner scopes exist. Resolve them before bootstrapping one operator."
                )
            tenant = tenants[0] if tenants else None
            if tenant is not None and tenant.slug != slug:
                raise SystemExit(
                    "An owner scope already exists. Use its slug when bootstrapping the operator."
                )
            if tenant is None:
                tenant = Tenant(
                    name=args.name.strip() or "Site Panel Operator",
                    slug=slug,
                    branding={},
                    quotas={},
                )
                db.add(tenant)
                await db.flush()
            user = User(
                email=email,
                password_hash=hash_password(password),
                role="superadmin",
                tenant_id=tenant.id,
            )
            db.add(user)
        else:
            tenant = (
                await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
            ).scalar_one_or_none()
            if tenant is None:
                raise SystemExit(
                    "The existing operator has no owner scope. Repair the database first."
                )
            user.password_hash = hash_password(password)
            user.role = "superadmin"
            user.is_active = True
            action = "operator.password_reset"

        await append_audit(
            db,
            action=action,
            payload={"email": email},
            tenant_id=tenant.id,
            actor_id=user.id,
        )
        await db.commit()

    print(f"Operator ready: {email}")
    print(f"Internal owner: {tenant.slug}")


def main() -> None:
    args = parse_args()
    asyncio.run(bootstrap(args, read_password(args.password_stdin)))


if __name__ == "__main__":
    main()
