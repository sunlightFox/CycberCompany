import json
import sqlite3
import sys


def main() -> None:
    con = sqlite3.connect(sys.argv[1])
    con.row_factory = sqlite3.Row
    print(
        "turn_status",
        [
            dict(r)
            for r in con.execute(
                "select status,error_code,count(*) c from chat_turns group by status,error_code"
            )
        ],
    )
    print("recent")
    for row in con.execute(
        "select turn_id,trace_id,status,error_code,created_at,updated_at "
        "from chat_turns order by created_at desc limit 10"
    ):
        print(json.dumps(dict(row), ensure_ascii=False))
    print("failed")
    for row in con.execute(
        "select turn_id,trace_id,status,error_code,error_summary "
        "from chat_turns where status=?",
        ("failed",),
    ):
        print(json.dumps(dict(row), ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
