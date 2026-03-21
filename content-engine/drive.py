"""
Google Drive interface — reads from Raw Input folder, writes to Ready to Post folder.
"""

import os
import io
import json
import logging
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']

# Supported input file types
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.m4v', '.webm'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.heif'}


def get_service():
    """Build and return an authenticated Drive service."""
    creds_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not creds_json:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON environment variable not set")

    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=SCOPES
    )
    return build('drive', 'v3', credentials=creds)


def list_new_files(service, folder_id, processed_ids: set) -> list:
    """
    List all video/image files in the input folder that haven't been processed yet.
    Returns list of file metadata dicts.
    """
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType, createdTime, size)",
        orderBy="createdTime desc",
        pageSize=50
    ).execute()

    files = results.get('files', [])
    new_files = []

    for f in files:
        if f['id'] in processed_ids:
            continue
        name = f['name'].lower()
        ext = os.path.splitext(name)[1]
        if ext in VIDEO_EXTENSIONS or ext in IMAGE_EXTENSIONS:
            new_files.append(f)
            logger.info(f"New file found: {f['name']}")

    return new_files


def download_file(service, file_id: str, file_name: str, dest_dir: str) -> str:
    """Download a file from Drive to a local temp directory. Returns local path."""
    os.makedirs(dest_dir, exist_ok=True)
    local_path = os.path.join(dest_dir, file_name)

    request = service.files().get_media(fileId=file_id)
    with io.FileIO(local_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                logger.info(f"Downloading {file_name}: {int(status.progress() * 100)}%")

    logger.info(f"Downloaded: {local_path}")
    return local_path


def get_or_create_subfolder(service, parent_folder_id: str, folder_name: str) -> str:
    """Get existing subfolder ID or create it. Returns folder ID."""
    query = (
        f"'{parent_folder_id}' in parents and "
        f"name = '{folder_name}' and "
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"trashed = false"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get('files', [])

    if files:
        return files[0]['id']

    # Create it
    folder_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_folder_id]
    }
    folder = service.files().create(body=folder_metadata, fields='id').execute()
    logger.info(f"Created Drive folder: {folder_name}")
    return folder['id']


def upload_file(service, local_path: str, folder_id: str, file_name: str = None) -> str:
    """Upload a local file to a Drive folder. Returns the new file ID."""
    name = file_name or os.path.basename(local_path)
    ext = os.path.splitext(name)[1].lower()

    mime_map = {
        '.mp4': 'video/mp4',
        '.mov': 'video/quicktime',
        '.txt': 'text/plain',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
    }
    mime_type = mime_map.get(ext, 'application/octet-stream')

    file_metadata = {'name': name, 'parents': [folder_id]}
    media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)

    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, name'
    ).execute()

    logger.info(f"Uploaded to Drive: {name} (id: {uploaded['id']})")
    return uploaded['id']


def upload_text(service, content: str, file_name: str, folder_id: str) -> str:
    """Upload a text string as a .txt file to Drive. Returns file ID."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(content)
        tmp_path = f.name
    try:
        file_id = upload_file(service, tmp_path, folder_id, file_name)
    finally:
        os.unlink(tmp_path)
    return file_id
