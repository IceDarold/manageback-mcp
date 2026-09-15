"""Provision only the broker credential needed by this external MCP service.

Run as root on the deployment host. Never print credentials.
"""
import os
import pwd
from pathlib import Path


def main():
    values = {}
    for line in Path("/etc/server-exec/auth.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    secret = values.get("APPROVER_INTERNAL_SECRET")
    if not secret:
        raise SystemExit("OAuth broker internal secret is not configured")
    target = Path("/etc/manageback-mcp/auth.env")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(f"APPROVER_INTERNAL_SECRET={secret}\n")
        port = int(values.get("APPROVER_PORT") or "8102")
        stream.write(f"OAUTH_ISSUER=https://auth.archik.tech\nAPPROVER_URL=http://127.0.0.1:{port}\n")
        stream.write("MANAGEBAC_PUBLIC_ORIGIN=https://managebac.archik.tech\nMANAGEBAC_DATA_DIR=/var/lib/manageback-mcp\n")
    os.chmod(target, 0o600)
    directory = Path("/var/lib/manageback-mcp")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    user = pwd.getpwnam("lifeos-platform")
    os.chown(directory, user.pw_uid, user.pw_gid)
    print("ManageBac OAuth configuration provisioned")


if __name__ == "__main__":
    main()
