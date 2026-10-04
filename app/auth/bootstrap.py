"""Create the first administrator from an interactive, hidden password prompt."""

import asyncio
import getpass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select

from app.auth.security import hash_password
from app.core.config import settings
from app.db.persistence import validate_database_url
from app.db.session import SessionFactory
from app.models import UserAccountRecord


async def main() -> int:
    validate_database_url(settings.database_url)
    username = input("Initial administrator username: ").strip().lower()
    password = getpass.getpass("Administrator password (hidden): ")
    confirmation = getpass.getpass("Confirm password (hidden): ")
    if password != confirmation:
        print("Passwords did not match.")
        return 2
    try:
        encoded = hash_password(password)
    except ValueError as error:
        print(str(error))
        return 2
    async with SessionFactory.begin() as session:
        count = await session.scalar(select(func.count()).select_from(UserAccountRecord))
        if count:
            print("Administrator bootstrap refused: user accounts already exist.")
            return 2
        session.add(UserAccountRecord(user_id=str(uuid4()), username=username,
                                      password_hash=encoded, role="admin", is_active=True,
                                      created_at=datetime.now(timezone.utc)))
    print("Initial administrator created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
