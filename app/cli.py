"""
Cognifolio Administrative & Identity Security CLI

Provides server-side role management and provisioning without web backdoors.
Strictly executable via terminal / SSH with infrastructure access.

Usage:
    python -m app.cli promote-user --email dev@company.com --role developer
    python -m app.cli create-user --email admin@company.com --name "Security Lead" --role admin --password "StrongPass123!"
    python -m app.cli list-users
"""

import argparse
import sys
import uuid
from app.database import SessionLocal
from app.models.entities import User, AuditLog
from app.core.security import hash_password


def promote_user(email: str, role: str) -> None:
    role = role.lower().strip()
    if role not in ["developer", "admin", "analyst", "guest"]:
        print(f"Error: Invalid role '{role}'. Allowed roles: developer, admin, analyst, guest.")
        sys.exit(1)

    clean_email = email.lower().strip()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == clean_email).first()
        if not user:
            print(f"Error: User with email '{clean_email}' not found. Create user first.")
            sys.exit(1)

        prev_role = user.role
        user.role = role
        user.is_guest = False

        audit = AuditLog(
            id=str(uuid.uuid4()),
            action="role_promoted_via_cli",
            target_type="user",
            target_id=str(user.id),
            user_id=None,
            metadata={"email": clean_email, "previous_role": prev_role, "new_role": role},
        )
        db.add(audit)
        db.commit()
        print(f"Successfully updated '{clean_email}' role: {prev_role} -> {role}")
    except Exception as e:
        db.rollback()
        print(f"Database error during promotion: {e}")
        sys.exit(1)
    finally:
        db.close()


def create_user(email: str, name: str, role: str, password: str) -> None:
    role = role.lower().strip()
    if role not in ["developer", "admin", "analyst"]:
        print(f"Error: Invalid role '{role}'.")
        sys.exit(1)

    clean_email = email.lower().strip()
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == clean_email).first()
        if existing:
            print(f"User '{clean_email}' already exists. Use promote-user instead.")
            sys.exit(1)

        pwd_hash = hash_password(password)
        new_user = User(
            id=str(uuid.uuid4()),
            email=clean_email,
            name=name,
            password_hash=pwd_hash,
            role=role,
            is_guest=False,
        )
        db.add(new_user)
        audit = AuditLog(
            id=str(uuid.uuid4()),
            action="user_created_via_cli",
            target_type="user",
            target_id=str(new_user.id),
            user_id=None,
            metadata={"email": clean_email, "role": role},
        )
        db.add(audit)
        db.commit()
        print(f"Successfully created user '{clean_email}' with role '{role}'.")
    except Exception as e:
        db.rollback()
        print(f"Database error: {e}")
        sys.exit(1)
    finally:
        db.close()


def list_users() -> None:
    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.created_at.desc()).limit(100).all()
        print(f"{'EMAIL':<35} | {'NAME':<25} | {'ROLE':<12} | {'IS_GUEST'}")
        print("-" * 85)
        for u in users:
            print(f"{u.email:<35} | {u.name:<25} | {u.role:<12} | {u.is_guest}")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Cognifolio Enterprise Identity & Access Management CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # promote-user
    promote_parser = subparsers.add_parser("promote-user", help="Promote a user to developer/admin")
    promote_parser.add_argument("--email", required=True, help="User email address")
    promote_parser.add_argument("--role", default="developer", choices=["developer", "admin", "analyst", "guest"], help="New role")

    # create-user
    create_parser = subparsers.add_parser("create-user", help="Provision a new enterprise user")
    create_parser.add_argument("--email", required=True, help="User email address")
    create_parser.add_argument("--name", default="Cognifolio Engineer", help="Full name")
    create_parser.add_argument("--role", default="developer", choices=["developer", "admin", "analyst"], help="User role")
    create_parser.add_argument("--password", required=True, help="Password for user")

    # list-users
    subparsers.add_parser("list-users", help="List active users and assigned roles")

    args = parser.parse_args()
    if args.command == "promote-user":
        promote_user(args.email, args.role)
    elif args.command == "create-user":
        create_user(args.email, args.name, args.role, args.password)
    elif args.command == "list-users":
        list_users()


if __name__ == "__main__":
    main()
