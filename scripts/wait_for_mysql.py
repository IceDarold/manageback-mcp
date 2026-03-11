#!/usr/bin/env python3
"""Wait until MySQL accepts connections."""

from __future__ import annotations

import os
import time

import pymysql


def main() -> int:
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "managebac")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DB", "managebac_mcp")
    timeout_s = int(os.getenv("MYSQL_WAIT_TIMEOUT", "90"))

    start = time.time()
    while time.time() - start < timeout_s:
        try:
            conn = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                connect_timeout=3,
            )
            conn.close()
            print("MySQL is ready")
            return 0
        except Exception:
            time.sleep(2)

    print(f"MySQL not ready after {timeout_s}s")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
