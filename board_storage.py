"""Storage backends for the classroom board."""

import json
import os
from functools import lru_cache
from heapq import nsmallest
from itertools import islice
from pathlib import Path
import re

MAX_POSTS = 100
PARTITION = "posts"
ID_PATTERN = re.compile(r"[0-9]{19}-[a-f0-9]{32}")


class FileBoard:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def list_posts(self):
        latest = nsmallest(MAX_POSTS, self.directory.glob("*.json"))
        return [json.loads(path.read_text(encoding="utf-8")) for path in latest]

    def save(self, post, photo):
        post_id = post["id"]
        image_path = self.directory / f"{post_id}.jpg"
        pending = self.directory / f"{post_id}.pending"
        try:
            if photo is not None:
                image_path.write_bytes(photo)
            pending.write_text(json.dumps(post, ensure_ascii=False), encoding="utf-8")
            # Readers see a post only after its picture and metadata are complete.
            pending.replace(self.directory / f"{post_id}.json")
        except OSError:
            pending.unlink(missing_ok=True)
            image_path.unlink(missing_ok=True)
            raise

    def image(self, post_id):
        return (self.directory / f"{post_id}.jpg").read_bytes()


class AzureBoard:
    def __init__(self, account):
        from azure.data.tables import TableClient
        from azure.identity import ManagedIdentityCredential
        from azure.storage.blob import ContainerClient

        if not re.fullmatch(r"[a-z0-9]{3,24}", account):
            raise ValueError("Invalid Azure Storage account name.")

        identity = ManagedIdentityCredential()
        self.table = TableClient(
            f"https://{account}.table.core.windows.net",
            os.getenv("TABLE_NAME", "pocketposts"),
            credential=identity,
            retry_total=2,
        )
        self.container = ContainerClient(
            f"https://{account}.blob.core.windows.net",
            os.getenv("BLOB_CONTAINER", "pocketimages"),
            credential=identity,
            retry_total=2,
        )

    def list_posts(self):
        records = self.table.query_entities(
            query_filter="PartitionKey eq @pk",
            parameters={"pk": PARTITION},
            results_per_page=MAX_POSTS,
        )
        # Inverse-time RowKeys sort the newest posts first on the server.
        posts = []
        for record in islice(records, MAX_POSTS):
            post = {key: record[key] for key in (
                "title", "body", "minutes", "category", "alias", "createdAt"
            )}
            post["id"] = record["RowKey"]
            post["imageUrl"] = f"/api/images/{post['id']}" if record.get("hasImage") else None
            posts.append(post)
        return posts

    def save(self, post, photo):
        from azure.core.exceptions import HttpResponseError, ResourceExistsError
        from azure.storage.blob import ContentSettings

        entity = dict(post)
        entity["PartitionKey"] = PARTITION
        entity["RowKey"] = entity.pop("id")
        entity["hasImage"] = bool(entity.pop("imageUrl"))
        blob = self.container.get_blob_client(f"{entity['RowKey']}.jpg")
        if photo is not None:
            blob.upload_blob(photo, content_settings=ContentSettings(content_type="image/jpeg"))
        try:
            self.table.create_entity(entity)
        except ResourceExistsError:
            # A retry after a lost response may find this unique row already committed.
            existing = self.table.get_entity(PARTITION, entity["RowKey"])
            if any(existing.get(key) != value for key, value in entity.items()):
                raise
        except HttpResponseError as exc:
            # A timeout or server error can follow a successful commit. Preserve its image.
            if photo is not None and exc.status_code in (400, 401, 403, 404, 413):
                blob.delete_blob()
            raise

    def image(self, post_id):
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self.container.download_blob(f"{post_id}.jpg").readall()
        except ResourceNotFoundError as exc:
            if exc.error_code == "BlobNotFound":
                raise FileNotFoundError from exc
            raise


@lru_cache(maxsize=1)
def get_board():
    account = os.getenv("STORAGE_ACCOUNT_NAME", "").strip()
    if account:
        return AzureBoard(account)
    if os.getenv("WEBSITE_SITE_NAME"):
        return FileBoard("/home/data/houkago-pocket")
    return FileBoard(Path(__file__).resolve().parent / "instance")
