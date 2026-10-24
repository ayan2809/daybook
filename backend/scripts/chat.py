"""A full reflection in the terminal: the fastest way to test the engine (plan 16, test C2).

    make chat        # fake LLM
    make chat-live   # real Gemini

Commands: /end ends the session, /time shows seconds left. Uses the owner vault in DAYBOOK_DATA_DIR.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

from daybook.engine import conductor
from daybook.engine import state as st
from daybook.engine.app import OWNER, App
from daybook.memory import repo


async def main() -> None:
    app = App(tts=None)
    mode = (input("mode [guided/dump/quick] (guided): ").strip() or "guided")
    try:
        s = await conductor.start_session(app, OWNER, mode, datetime.now(UTC))
    except conductor.SessionError as exc:
        if exc.code != "session_active":
            raise
        await conductor.end_session(app, OWNER, exc.extra["session_id"], "user", datetime.now(UTC))
        await app.drain()
        s = await conductor.start_session(app, OWNER, mode, datetime.now(UTC))
    print(f"\nDaybook: {s.turns[-1].text}\n")
    while True:
        text = input("you> ").strip()
        if not text:
            continue
        if text == "/time":
            loaded = st.load(app.store, OWNER.prefix, s.id)[0]
            print(f"  {loaded.seconds_left(datetime.now(UTC))} s left")
            continue
        if text == "/end":
            await conductor.end_session(app, OWNER, s.id, "user", datetime.now(UTC))
            break
        try:
            r = await conductor.handle_turn(app, OWNER, s.id, uuid.uuid4().hex, text, len(text),
                                            datetime.now(UTC))
        except conductor.SessionError as exc:
            print(f"  [{exc.code}] {exc.message}")
            break
        chips = ", ".join(f"{i['title'][:40]} [{i['chip']}]" for i in r["board"]["items"])
        print(f"\nDaybook: {r['reply_text']}\n  ({r['phase']}, {r['seconds_left']} s left; board: {chips})\n")
        if r["ended"]:
            break
    print("\nFinalizing…")
    await app.drain()
    loaded = st.load(app.store, OWNER.prefix, s.id)[0]
    print(f"status: {loaded.status}\n")
    if loaded.summary:
        print(loaded.summary["day_summary"], "\n")
        with app.vault(OWNER).read() as conn:
            for it in repo.list_items(conn, ids=loaded.summary["item_ids"], include_hidden=True):
                print(f"- [{it['kind']}/{it['status']}] {it['title']}")
                if it["bullet"]:
                    print(f"    {it['bullet']}")
                for m in it["metrics"]:
                    print(f"    {m['name']}: {m['display']}")
                if it["validation_note"]:
                    print(f"    note: {it['validation_note']}")
            for p in loaded.summary["pending_created"]:
                print(f"? {p['question']}")
        print(f"\ncost ≈ ${loaded.budgets['cost_usd']:.4f}; latencies (ms): {loaded.turn_latencies}")
        print(json.dumps(loaded.guard_fails) if loaded.guard_fails else "guards: no failures")


if __name__ == "__main__":
    asyncio.run(main())
