"""Start one application worker on the local or cloud-assigned port."""

import os
from pathlib import Path
import pwd


if __name__ == "__main__":
    # Railway mounts volumes as root; hand SQLite storage to the application user.
    if os.geteuid() == 0:
        account = pwd.getpwnam("app")
        database = Path(os.environ["DATABASE_PATH"])
        database.parent.mkdir(parents=True, exist_ok=True)
        os.chown(database.parent, account.pw_uid, account.pw_gid)
        for path in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")):
            if path.exists():
                os.chown(path, account.pw_uid, account.pw_gid)
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)

    os.execvp("uvicorn", [
        "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0",
        "--port", os.getenv("PORT", "8000"), "--workers", "1",
    ])
