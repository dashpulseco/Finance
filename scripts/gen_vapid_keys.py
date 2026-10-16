"""Generate a VAPID keypair for Web Push.

Run once, by path, from the Finan folder:

    py -3 scripts\\gen_vapid_keys.py

The PUBLIC key goes to site/push-config.json, which is published to a public GitHub Pages site -
that is fine and necessary, an applicationServerKey is meant to be public and cannot send a push
on its own. The PRIVATE key goes to secrets/vapid_private_key.txt (gitignored) and is printed
once so it can be pasted into the VAPID_PRIVATE_KEY repository secret. It is never written under
site/ and never committed.

The VAPID *subject* is deliberately not written or published here: it may legitimately be a
mailto: address, so publishing it would put an email address on a public site. It lives only in
config.json under notifications.web_push.vapid_subject, which this script never touches.
"""
import sys
from pathlib import Path

# Make `import finan` work when this file is run by path (py -3 scripts/gen_vapid_keys.py) from
# any cwd: running a script puts its own folder on sys.path, not the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse      # noqa: E402  (the bootstrap above must run before the finan imports)
import base64        # noqa: E402
import json          # noqa: E402

from cryptography.hazmat.primitives import serialization                      # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec                      # noqa: E402

from finan.settings import BASE_DIR, PUSH_CONFIG_PATH, SECRETS_DIR            # noqa: E402

PRIVATE_KEY_NAME = "vapid_private_key.txt"
REKEY_WARNING = ("Re-keying invalidates every existing push subscription: each phone has to tap "
                 "\"Enable iPhone notifications\" again.")


def _rel(path: Path) -> str:
    """Path relative to the project root.

    Every path this script prints is relative, like every path in the committed documentation:
    these lines get pasted into public issues and an absolute path would carry the user's Windows
    account name with them.
    """
    try:
        return str(path.relative_to(BASE_DIR))
    except ValueError:                       # pragma: no cover - only if someone moves the tree
        return path.name


def b64url(raw: bytes) -> str:
    """base64url with the padding stripped, which is the only encoding Web Push accepts."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_keypair() -> tuple:
    """Return (private_b64url, public_b64url): 43 chars and 87 chars starting with 'B'.

    Uses cryptography's primitive API rather than py_vapid.Vapid.generate_keys(), because the
    pinned py-vapid passes the curve *class* to ec.generate_private_key(), which already emits
    CryptographyDeprecationWarning on cryptography 45 and is documented to become an exception.
    Doing it here also pins the exact encodings: the raw 32-byte big-endian private scalar and
    the 65-byte X9.62 uncompressed public point.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    private_raw = key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = key.public_key().public_bytes(serialization.Encoding.X962,
                                               serialization.PublicFormat.UncompressedPoint)
    return b64url(private_raw), b64url(public_raw)


def published_key(path: Path) -> str:
    """The public key currently in site/push-config.json, or "" when there is none.

    The committed placeholder holds an empty string so the app loads and the panel reports
    `keys-missing` honestly before setup, which is why bare existence of the file cannot be an
    obstacle: only a NON-EMPTY key is something --force would overwrite.
    """
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    value = data.get("vapid_public_key")
    return value if isinstance(value, str) else ""


def obstacles(secret_path: Path) -> list:
    """What --force would destroy, as printable sentences."""
    found = []
    if secret_path.exists():
        found.append(f"{_rel(secret_path)} already holds a private key")
    if published_key(PUSH_CONFIG_PATH):
        found.append(f"{_rel(PUSH_CONFIG_PATH)} already holds a public key")
    return found


def write_push_config(public: str) -> None:
    """site/push-config.json is PUBLIC. Only the applicationServerKey belongs in it."""
    PUSH_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PUSH_CONFIG_PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"vapid_public_key": public}, fh, indent=1)
        fh.write("\n")


def write_private_key(path: Path, private: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(private + "\n", encoding="utf-8", newline="\n")
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):   # Windows ignores the mode; POSIX honours it
        pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Generate a VAPID keypair for Finan's Web Push")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing keypair (invalidates every subscription)")
    parser.add_argument("--print-only", action="store_true",
                        help="print the private key instead of writing secrets/; "
                             "site/push-config.json is still written")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    secret_path = SECRETS_DIR / PRIVATE_KEY_NAME

    blocking = obstacles(secret_path)
    if blocking and not args.force:
        print("Refusing to generate a new keypair:")
        for item in blocking:
            print(f"  - {item}")
        print(REKEY_WARNING)
        print("Pass --force if that is what you want.")
        return 1

    private, public = generate_keypair()
    write_push_config(public)

    print("Generated a new VAPID keypair.")
    print("")
    print(f"Public key ({len(public)} chars, published with the site):")
    print(f"  {public}")
    print(f"  written to {_rel(PUSH_CONFIG_PATH)}")
    print("")
    print(f"Private key ({len(private)} chars, SECRET - shown only now):")
    print(f"  {private}")
    if args.print_only:
        print("  not written to disk (--print-only)")
    else:
        write_private_key(secret_path, private)
        print(f"  written to {_rel(secret_path)} - gitignored, never commit it")
    print("")
    print("Paste that private key into the repository secret VAPID_PRIVATE_KEY:")
    print("  on github.com open your repository, then Settings -> Secrets and variables ->")
    print("  Actions -> New repository secret, name it VAPID_PRIVATE_KEY and paste the key.")
    if blocking:
        print(REKEY_WARNING)
    print("")
    print("Next: publish the new public key (it is only on this PC so far). From the Finan "
          "folder:")
    print("  powershell -ExecutionPolicy Bypass -File .\\deploy.ps1 -RemoteUrl "
          "https://github.com/<you>/finan -CommitMessage \"Publish VAPID public key\" -Force")
    print("then open the Actions tab and click Run workflow - that is what puts the key on the "
          "site.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
