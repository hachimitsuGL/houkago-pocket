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
            if photo is not None:
                image_path.unlink(missing_ok=True)
            raise

    def image(self, post_id):
        return (self.directory / f"{post_id}.jpg").read_bytes()


def blob_container(account, identity):
    from azure.storage.blob import ContainerClient

    if not re.fullmatch(r"[a-z0-9]{3,24}", account):
        raise ValueError("Invalid Azure Storage account name.")
    return ContainerClient(
        f"https://{account}.blob.core.windows.net",
        os.getenv("BLOB_CONTAINER", "pocketimages"),
        credential=identity,
        retry_total=2,
    )


def upload_photo(blob, photo):
    from azure.storage.blob import ContentSettings

    return blob.upload_blob(photo, overwrite=False, content_settings=ContentSettings(content_type="image/jpeg"))


class MissingBlob(FileNotFoundError):
    """An Azure BlobNotFound response, distinct from a local file error."""


def download_photo(container, post_id):
    from azure.core.exceptions import ResourceNotFoundError

    try:
        return container.download_blob(f"{post_id}.jpg").readall()
    except ResourceNotFoundError as exc:
        if exc.error_code == "BlobNotFound":
            raise MissingBlob from exc
        raise


class BlobFileBoard(FileBoard):
    """Keep file-backed posts while storing and serving their pictures through Azure."""

    def __init__(self, directory, account):
        from azure.identity import ManagedIdentityCredential

        super().__init__(directory)
        self.container = blob_container(account, ManagedIdentityCredential())

    def save(self, post, photo):
        if photo is None:
            return super().save(post, None)

        from azure.core import MatchConditions

        post_id = post["id"]
        blob = self.container.get_blob_client(f"{post_id}.jpg")
        uploaded = upload_photo(blob, photo)
        try:
            super().save(post, None)
        except OSError:
            # A network drive may report failure after committing a rename.
            # Clean up only an uncommitted post and the exact blob we created.
            if not (self.directory / f"{post_id}.json").exists():
                blob.delete_blob(etag=uploaded["etag"], match_condition=MatchConditions.IfNotModified)
            raise

    def image(self, post_id):
        from azure.core.exceptions import ResourceExistsError
        from io import BytesIO
        from PIL import Image

        # Only a published, image-bearing post can expose or migrate a picture.
        record = json.loads((self.directory / f"{post_id}.json").read_text(encoding="utf-8"))
        if record.get("id") != post_id or record.get("imageUrl") != f"/api/images/{post_id}":
            raise FileNotFoundError
        try:
            return download_photo(self.container, post_id)
        except MissingBlob:
            legacy_photo = super().image(post_id)

        # FileBoard stored JPEGs resized to this bound. Validate older files
        # before turning them into public content served by the proxy.
        with Image.open(BytesIO(legacy_photo), formats=("JPEG",)) as picture:
            if max(picture.size) > 1400:
                raise ValueError("Unexpected dimensions for a saved board image.")
            picture.load()

        blob = self.container.get_blob_client(f"{post_id}.jpg")
        try:
            upload_photo(blob, legacy_photo)
        except ResourceExistsError as exc:
            # A concurrent first view can migrate the same immutable photo.
            if exc.error_code != "BlobAlreadyExists":
                raise
        return download_photo(self.container, post_id)


class AzureBoard:
    def __init__(self, account):
        from azure.data.tables import TableClient
        from azure.identity import ManagedIdentityCredential

        identity = ManagedIdentityCredential()
        self.container = blob_container(account, identity)
        self.table = TableClient(
            f"https://{account}.table.core.windows.net",
            os.getenv("TABLE_NAME", "pocketposts"),
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

        entity = dict(post)
        entity["PartitionKey"] = PARTITION
        entity["RowKey"] = entity.pop("id")
        entity["hasImage"] = bool(entity.pop("imageUrl"))
        blob = self.container.get_blob_client(f"{entity['RowKey']}.jpg")
        if photo is not None:
            upload_photo(blob, photo)
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
        return download_photo(self.container, post_id)


@lru_cache(maxsize=1)
def get_board():
    account = os.getenv("STORAGE_ACCOUNT_NAME", "").strip()
    if account:
        return AzureBoard(account)
    if os.getenv("WEBSITE_SITE_NAME"):
        directory = Path("/home/data/houkago-pocket")
    else:
        directory = Path(__file__).resolve().parent / "instance"
    image_account = os.getenv("BLOB_ACCOUNT_NAME", "").strip()
    if image_account:
        return BlobFileBoard(directory, image_account)
    return FileBoard(directory)
