#!/usr/bin/env python3
"""Create the offline demo investigation without going through the API.

Usage:  python3 scripts/seed_demo.py [--email you@example.com] [--password ...]

Useful for a first look at the UI, or for a demo on a machine with no network access.
Every value in the dataset is synthetic and refers to no real person.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select

from app.core.db import create_all, dispose_engine, get_sessionmaker
from app.core.enums import Role
from app.demo.seed import seed_demo_investigation
from app.models import User
from app.security.auth import hash_password


async def main(email: str, username: str, password: str) -> int:
    await create_all()
    async with get_sessionmaker()() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                id=uuid.uuid4(),
                email=email,
                username=username,
                password_hash=hash_password(password),
                role=str(Role.ADMIN),
            )
            session.add(user)
            await session.flush()
            print(f"created admin user '{username}'")

        investigation = await seed_demo_investigation(session, user.id)
        await session.commit()

        print(f"seeded demo investigation: {investigation.name}")
        print(f"  id: {investigation.id}")
        print(f"  sign in as: {username} / {password}")
    await dispose_engine()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="analyst@example.com")
    parser.add_argument("--username", default="analyst")
    parser.add_argument("--password", default="demo-password-please-change")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.email, args.username, args.password)))
