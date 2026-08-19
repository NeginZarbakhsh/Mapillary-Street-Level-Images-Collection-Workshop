"""Upload files to Azure Blob Storage, and read them back by URL.

This is UNEXERCISED against a real Azure account -- there's no live storage
account in this environment to test against. It's written against the
current, stable azure-storage-blob SDK (checked directly against the
installed package, not from memory). Test it against your real sandbox
storage account before relying on it.

Setup (see the step-by-step in README.md for the portal side of this):
    export AZURE_STORAGE_CONNECTION_STRING="<from the portal, Access keys>"

Usage:
    python3 azure_blob.py upload text_output --container contracts
    python3 azure_blob.py list --container contracts
    python3 azure_blob.py url mydoc.txt --container contracts --hours 24
    python3 azure_blob.py download mydoc.txt --container contracts --out downloaded.txt
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    generate_blob_sas,
)


def _client() -> BlobServiceClient:
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        print(
            "AZURE_STORAGE_CONNECTION_STRING is not set. Get it from the Azure "
            "portal: your storage account -> Access keys -> key1 -> Connection "
            "string. See README.md step 3.",
            file=sys.stderr,
        )
        sys.exit(1)
    return BlobServiceClient.from_connection_string(conn_str)


def upload(folder: str, container: str) -> None:
    """Upload every file in `folder` into `container` (created if missing)."""
    client = _client()
    container_client = client.get_container_client(container)
    if not container_client.exists():
        # public_access=None -- private by default. These may be sensitive
        # documents; nothing here is made publicly readable.
        container_client.create_container()
        print(f"Created container '{container}' (private)")

    files = sorted(Path(folder).glob("*"))
    files = [f for f in files if f.is_file()]
    if not files:
        print(f"No files found in {folder}/")
        return

    for path in files:
        with open(path, "rb") as f:
            container_client.upload_blob(name=path.name, data=f, overwrite=True)
        print(f"  uploaded  {path.name}")

    print(f"\n{len(files)} file(s) uploaded to container '{container}'.")
    print("These blob URLs will NOT work directly in a browser -- the container")
    print("is private. Use `url` (for a temporary link) or `download` instead.")


def list_blobs(container: str) -> None:
    client = _client()
    container_client = client.get_container_client(container)
    names = [b.name for b in container_client.list_blobs()]
    if not names:
        print(f"Container '{container}' is empty (or doesn't exist yet).")
        return
    for name in names:
        print(f"  {name}")
    print(f"\n{len(names)} file(s) in '{container}'.")


def sas_url(blob_name: str, container: str, hours: int) -> str:
    """Build a time-limited, read-only URL for one blob.

    This is the answer to "how do I get it from a URL": the container stays
    private, but this link works on its own (in a browser, in `requests`, in
    another script) for exactly `hours` hours, then stops working.
    """
    client = _client()
    account_name = client.account_name
    account_key = client.credential.account_key

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=hours),
    )
    return f"https://{account_name}.blob.core.windows.net/{container}/{blob_name}?{sas_token}"


def download(blob_name: str, container: str, out_path: str) -> None:
    """Fetch a blob straight through the SDK -- no URL needed for this path."""
    client = _client()
    container_client = client.get_container_client(container)
    data = container_client.download_blob(blob_name).readall()
    Path(out_path).write_bytes(data)
    print(f"Downloaded {blob_name} -> {out_path} ({len(data):,} bytes)")


def download_all(container: str, out_folder: str) -> None:
    """Pull every blob in a container down into a local folder.

    This is the step before chunking: chunk_text.py reads local .txt files,
    not blobs directly, so if you uploaded from a different machine (or lost
    the local copies), this gets them back first.
    """
    client = _client()
    container_client = client.get_container_client(container)
    out_dir = Path(out_folder)
    out_dir.mkdir(parents=True, exist_ok=True)

    names = [b.name for b in container_client.list_blobs()]
    if not names:
        print(f"Container '{container}' is empty.")
        return

    for name in names:
        data = container_client.download_blob(name).readall()
        (out_dir / name).write_bytes(data)
        print(f"  downloaded  {name}")

    print(f"\n{len(names)} file(s) downloaded to {out_dir}/")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_upload = sub.add_parser("upload", help="Upload every file in a folder")
    p_upload.add_argument("folder")
    p_upload.add_argument("--container", required=True)

    p_list = sub.add_parser("list", help="List files in a container")
    p_list.add_argument("--container", required=True)

    p_url = sub.add_parser("url", help="Get a temporary, working URL for one file")
    p_url.add_argument("blob_name")
    p_url.add_argument("--container", required=True)
    p_url.add_argument("--hours", type=int, default=24, help="How long the link stays valid (default: 24)")

    p_download = sub.add_parser("download", help="Download one file by name")
    p_download.add_argument("blob_name")
    p_download.add_argument("--container", required=True)
    p_download.add_argument("--out", required=True)

    p_download_all = sub.add_parser("download-all", help="Download every file in a container")
    p_download_all.add_argument("--container", required=True)
    p_download_all.add_argument("--out", required=True)

    args = parser.parse_args()

    if args.command == "upload":
        upload(args.folder, args.container)
    elif args.command == "list":
        list_blobs(args.container)
    elif args.command == "url":
        link = sas_url(args.blob_name, args.container, args.hours)
        print(link)
    elif args.command == "download":
        download(args.blob_name, args.container, args.out)
    elif args.command == "download-all":
        download_all(args.container, args.out)


if __name__ == "__main__":
    main()
