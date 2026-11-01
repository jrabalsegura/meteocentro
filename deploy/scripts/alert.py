#!/usr/bin/env python3
"""Email the operator when AEMET pauses or its key is about to expire.

Runs on the host from a systemd timer, outside the containers, with the standard library only.
It reads the worker's local status (no provider HTTP) and sends through the host's sendmail.
"""

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

# Days before expiry; when several are already due only one message is sent.
THRESHOLDS = (14, 3, 1)
MADRID = ZoneInfo("Europe/Madrid")
STATUS_COMMAND = (
    "podman",
    "exec",
    "meteocentro-worker",
    "python",
    "-m",
    "meteocentro.worker",
    "--provider",
    "aemet",
    "--status",
)
RENEW = (
    "Para renovarla: pedir una clave nueva en "
    "https://opendata.aemet.es/centrodedescargas/altaUsuario, sustituir AEMET_API_KEY en "
    "worker.env, reiniciar meteocentro-worker.service y, si AEMET está en pausa, ejecutar "
    "`podman exec meteocentro-worker python -m meteocentro.worker --provider aemet --resume` "
    "(docs/OPERACION.md §6)."
)


def local(instant):
    return instant.astimezone(MADRID).strftime("%d-%m-%Y %H:%M")


def evaluate(status, state, now):
    """Return the messages due and the new state; pure so it can be tested without a host."""
    messages, new = [], {**state, "expiry_sent": list(state.get("expiry_sent", []))}
    expires = status.get("key_expires_at")
    if expires != state.get("key_expires_at"):
        # A different key restarts the countdown.
        new["key_expires_at"], new["expiry_sent"] = expires, []
    if expires:
        instant = datetime.fromisoformat(expires)
        days = (instant - now).total_seconds() / 86400
        due = [t for t in THRESHOLDS if days <= t and t not in new["expiry_sent"]]
        if due and days > 0:
            left = math.ceil(days)
            messages.append(
                (
                    f"Meteocentro: la clave de AEMET caduca en {left} día{'s' if left > 1 else ''}",
                    f"La clave de AEMET del servidor caduca el {local(instant)} (hora peninsular)."
                    f"\n\n{RENEW}\n",
                )
            )
            new["expiry_sent"] = sorted(set(new["expiry_sent"]) | set(due), reverse=True)
    paused = status.get("status") == "paused"
    reason = ((status.get("runtime") or {}).get("pause_reason") or "unknown") if paused else None
    if reason != state.get("paused"):
        if reason:
            hint = (
                RENEW
                if reason == "invalid_credentials"
                else ("Revisar el log del worker antes de reanudar con --resume.")
            )
            messages.append(
                (
                    f"Meteocentro: AEMET en pausa ({reason})",
                    f"El worker ha pausado AEMET con el motivo «{reason}» y no lo reanudará "
                    f"solo. No entran observaciones, previsiones ni avisos de AEMET.\n\n{hint}\n",
                )
            )
        elif state.get("paused"):
            messages.append(
                (
                    "Meteocentro: AEMET vuelve a funcionar",
                    f"AEMET ya no está en pausa (estado: {status.get('status')}).\n",
                )
            )
        new["paused"] = reason
    return messages, new


def read_status():
    output = subprocess.run(STATUS_COMMAND, check=True, capture_output=True, text=True).stdout
    return json.loads(output.strip().splitlines()[-1])


def send(sender, recipient, subject, body):
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    message.set_content(body)
    subprocess.run(
        ["/usr/sbin/sendmail", "-t", "-i", "-f", sender],
        input=message.as_bytes(),
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print, do not send or save")
    parser.add_argument("--test", action="store_true", help="send a test message and exit")
    args = parser.parse_args()
    recipient, sender = os.environ.get("ALERT_TO", ""), os.environ.get("ALERT_FROM", "")
    if not (recipient and sender) and not args.dry_run:
        print("ALERT_TO and ALERT_FROM are required", file=sys.stderr)
        return 2
    if args.test:
        send(
            sender,
            recipient,
            "Meteocentro: correo de prueba de los avisos",
            "Los avisos de Meteocentro llegan a esta dirección.\n",
        )
        return 0
    state_file = Path(os.environ.get("STATE_DIRECTORY", ".")) / "alerts.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    messages, new = evaluate(read_status(), state, datetime.now(UTC))
    for subject, body in messages:
        if args.dry_run:
            print(f"{subject}\n\n{body}")
        else:
            send(sender, recipient, subject, body)
    if not args.dry_run and new != state:
        # Save only after sending, so a failed delivery is retried on the next run.
        temporary = state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(new, indent=2) + "\n")
        temporary.replace(state_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
