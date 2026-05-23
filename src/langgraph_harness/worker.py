"""
Worker: pollea el WakeupStore y reanuda los grafos cuyas citas vencieron.

Es el "reloj externo" que mi harness (Claude Code) tiene de fábrica y que un
proceso LangGraph en-proceso no tiene. Corré esto como servicio de systemd:
sobrevive reboots y reanuda los waits que quedaron persistidos en SQLite.

    harness-worker                 # poll cada 5s
    harness-worker --interval 10   # poll cada 10s

El proceso que agenda el wait y este worker deben compartir el mismo db_dir.
"""

from __future__ import annotations

import argparse
import time

from .factory import build_default_harness, default_db_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker que reanuda waits persistidos.")
    parser.add_argument("--interval", type=float, default=5.0, help="Segundos entre polls.")
    parser.add_argument("--db-dir", default=None, help="Directorio de las DBs (default: ~/.langgraph-harness).")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Modelo del agente.")
    parser.add_argument("--once", action="store_true", help="Procesar lo vencido una vez y salir.")
    args = parser.parse_args()

    harness = build_default_harness(db_dir=args.db_dir, model=args.model, verbose=True)
    store = harness.wakeup_store
    assert store is not None

    print(f"worker arriba — db_dir={args.db_dir or default_db_dir()} interval={args.interval}s")

    while True:
        for wakeup in store.due():
            reason = wakeup.payload.get("reason", "")
            print(f"[{wakeup.thread_id}] cita vencida ({reason}) — reanudando.")
            try:
                harness.resume(wakeup.thread_id)
            except Exception as exc:  # noqa: BLE001 — un thread roto no debe tumbar el worker
                print(f"[{wakeup.thread_id}] error al reanudar: {exc!r}. Borro la cita igual.")
            finally:
                store.delete(wakeup.id)

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
