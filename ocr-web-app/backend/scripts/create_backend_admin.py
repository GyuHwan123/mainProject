"""Create or update an application admin using backend-owned credentials."""

from __future__ import annotations

import argparse
import getpass

import httpx

from app.core.security import get_password_hash
from app.services.supabase_service import supabase_service


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="Docunex Developer")
    args = parser.parse_args()
    email = args.email.strip().lower()
    password = getpass.getpass("Password: ")
    if len(password) < 8:
        raise ValueError("비밀번호는 8자 이상이어야 합니다.")

    password_hash = get_password_hash(password)
    existing = supabase_service.get_user_by_email(email)
    if existing:
        response = httpx.patch(
            f"{supabase_service.url}/rest/v1/{supabase_service.users_table}",
            params={"email": f"eq.{email}"},
            headers=supabase_service._service_headers(),
            json={
                "name": args.name,
                "password_hash": password_hash,
                "social_provider": "local",
                "social_id": email,
                "role": "ADMIN",
                "is_active": True,
            },
            timeout=20,
        )
        response.raise_for_status()
        action = "updated"
    else:
        supabase_service.create_user(
            name=args.name,
            email=email,
            password_hash=password_hash,
            provider="local",
            provider_id=email,
            role="ADMIN",
        )
        action = "created"
    print(f"Backend admin {action}: {email}")


if __name__ == "__main__":
    main()
