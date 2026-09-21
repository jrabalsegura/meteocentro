"""Local account bootstrap and recovery; passwords are never command arguments."""

import argparse
import getpass
import re
import sys
import warnings

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from meteocentro.auth import password_hash
from meteocentro.db import get_engine
from meteocentro.job_queue import db_now
from meteocentro.models import AdminSession, AdminUser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "password", "revoke"])
    parser.add_argument("username")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", args.username):
        parser.error("Usuario no válido")
    encoded = None
    if args.action != "revoke":
        if not sys.stdin.isatty():
            parser.error("Se requiere una terminal interactiva para introducir la contraseña")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Contraseña (mínimo 15 caracteres): ")
            confirmation = getpass.getpass("Repetir contraseña: ")
        if not 15 <= len(password) <= 1024 or password != confirmation:
            parser.error("Longitud incorrecta o contraseñas distintas")
        encoded = password_hash(password)
        del password, confirmation
    with Session(get_engine()) as db, db.begin():
        user = db.scalar(
            select(AdminUser).where(AdminUser.username == args.username).with_for_update()
        )
        if args.action == "create":
            if user:
                parser.error("El usuario ya existe")
            db.add(AdminUser(username=args.username, password_hash=encoded))
        else:
            if not user:
                parser.error("Usuario inexistente")
            if encoded:
                user.password_hash = encoded
            db.execute(
                update(AdminSession)
                .where(AdminSession.user_id == user.id, AdminSession.revoked_at.is_(None))
                .values(revoked_at=db_now(db))
            )
    print("Operación completada. Las contraseñas no se muestran ni se guardan en claro.")


if __name__ == "__main__":
    main()
