import os
import mimetypes
import urllib.parse
from pathlib import Path
from fastapi import FastAPI, Form, UploadFile, File, Request
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from google.cloud import storage
from google.api_core.exceptions import Conflict, NotFound
from fastapi.templating import Jinja2Templates

app = FastAPI()

# Set up Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Google Cloud Storage setup
BUCKET_NAME = "mybucket_trialkc"  # Default bucket
storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)

@app.post("/set-active-bucket")
async def set_active_bucket(bucket_name: str = Form(...)):
    global BUCKET_NAME, bucket
    BUCKET_NAME = bucket_name
    bucket = storage_client.bucket(BUCKET_NAME)  # Update the bucket object
    return RedirectResponse(url="/", status_code=303)

@app.get("/", response_class=HTMLResponse)
async def main_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "BUCKET_NAME": BUCKET_NAME})

@app.get("/select-active-bucket", response_class=HTMLResponse)
async def select_active_bucket(request: Request):
    try:
        buckets = storage_client.list_buckets()
        bucket_list = [bucket.name for bucket in buckets]
        print("Available Buckets:", bucket_list)

        if not bucket_list:
            return HTMLResponse("<h2>No buckets found in the active project.</h2>")
        bucket_options = "".join([f'<option value="{name}">{name}</option>' for name in bucket_list])
        return templates.TemplateResponse("select_active_bucket.html", {"request": request, "BUCKET_OPTIONS": bucket_list}  # Pass the list directly
)

    except Exception as e:
        return HTMLResponse(f"<h2>Error retrieving buckets: {str(e)}</h2>")

@app.get("/create-bucket-form", response_class=HTMLResponse)
async def create_bucket_form(request: Request):
    return templates.TemplateResponse("create_bucket.html", {"request": request})

@app.post("/create-bucket")
async def create_bucket(bucket_name: str = Form(...)):
    try:
        existing_bucket = storage_client.get_bucket(bucket_name)
        if existing_bucket:
            return HTMLResponse(f"<h2>Bucket '{bucket_name}' already exists.</h2>")
    except NotFound:
        new_bucket = storage_client.create_bucket(bucket_name)
        return HTMLResponse(f"<h2>Bucket '{new_bucket.name}' created successfully.</h2>")
    except Exception as e:
        return HTMLResponse(f"<h2>Error creating bucket: {str(e)}</h2>")

@app.get("/list-buckets", response_class=HTMLResponse)
async def list_buckets(request: Request):
    try:
        buckets = storage_client.list_buckets()
        bucket_list = [bucket.name for bucket in buckets]
        if not bucket_list:
            return HTMLResponse("<h2>No buckets found in the active project.</h2>")
        buckets_html = "".join([f"<li>{name}</li>" for name in bucket_list])
        return templates.TemplateResponse(
            "list_buckets.html",
            {"request": request, "BUCKETS": buckets_html}
        )
    except Exception as e:
        return HTMLResponse(f"<h2>Error retrieving buckets: {str(e)}</h2>")

@app.get("/delete-bucket-form", response_class=HTMLResponse)
async def delete_bucket_form(request: Request):
    return templates.TemplateResponse("delete_bucket.html", {"request": request})

@app.post("/delete-bucket")
async def delete_bucket(bucket_name: str = Form(...)):
    try:
        bucket_to_delete = storage_client.get_bucket(bucket_name)
        bucket_to_delete.delete()
        return HTMLResponse(f"<h2>Bucket '{bucket_name}' deleted successfully.</h2>")
    except NotFound:
        return HTMLResponse(f"<h2>Bucket '{bucket_name}' not found.</h2>")
    except Exception as e:
        return HTMLResponse(f"<h2>Error deleting bucket: {str(e)}</h2>")

@app.get("/upload-form", response_class=HTMLResponse)
async def upload_form(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})

def _upload_file_to_gcs(blob_name, file_obj, content_type):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(blob_name)
    blob.chunk_size = 10 * 1024 * 1024
    blob.upload_from_file(file_obj=file_obj, content_type=content_type)
    return {"message": f"File '{blob_name}' uploaded successfully in chunks."}

@app.post("/upload")
async def upload_large_file(file: UploadFile = File(...)):
    file_type = file.content_type or "application/octet-stream"
    result = _upload_file_to_gcs(file.filename, file.file, file_type)
    return HTMLResponse(f"<h2>{result['message']}</h2><p><a href='/'>Back to Home</a></p>")

@app.get("/upload-directory-form", response_class=HTMLResponse)
async def upload_directory_form(request: Request):
    return templates.TemplateResponse("upload_directory.html", {"request": request, "BUCKET_NAME": BUCKET_NAME})

@app.post("/upload-directory")
async def upload_directory(directory_path: str = Form(...)):
    try:
        if not os.path.isdir(directory_path):
            return HTMLResponse(f"<h2>Directory '{directory_path}' does not exist or is not a directory.</h2>")
        base_dir = os.path.basename(os.path.normpath(directory_path))
        uploaded_files = []
        for root, _, files in os.walk(directory_path):
            for filename in files:
                local_file_path = os.path.join(root, filename)
                relative_path = os.path.relpath(local_file_path, directory_path)
                blob_name = f"{base_dir}/{relative_path.replace(os.path.sep, '/')}"
                content_type = mimetypes.guess_type(local_file_path)[0] or "application/octet-stream"
                with open(local_file_path, "rb") as f:
                    result = _upload_file_to_gcs(blob_name, f, content_type)
                    uploaded_files.append(blob_name)
        return HTMLResponse(
            f"<h2>Uploaded {len(uploaded_files)} files successfully.</h2>"
            f"<ul>{''.join([f'<li>{file}</li>' for file in uploaded_files])}</ul>"
            f"<p><a href='/'>Back to Home</a></p>"
        )
    except Exception as e:
        return HTMLResponse(f"<h2>Error uploading directory: {str(e)}</h2>")

def safe_filename(filename: str) -> str:
    return urllib.parse.quote(filename)

@app.get("/retrieve-files", response_class=HTMLResponse)
async def retrieve_files(request: Request):
    try:
        current_bucket = storage_client.bucket(BUCKET_NAME)
        blobs = list(current_bucket.list_blobs())
        if not blobs:
            return HTMLResponse("<h2>No files found in the active bucket.</h2>")
        files = [{"name": blob.name, "encoded_name": safe_filename(blob.name)} for blob in blobs]
        return templates.TemplateResponse(
    "retrieve_files.html",
    {"request": request, "FILES": files}
)

    except Exception as e:
        return HTMLResponse(f"<h2>Error retrieving files: {str(e)}</h2>")

@app.get("/download-file/{filename:path}")
async def download_file(filename: Path):
    try:
        decoded_filename = urllib.parse.unquote(str(filename))
        current_bucket = storage_client.bucket(BUCKET_NAME)
        blob = current_bucket.blob(decoded_filename)

        if not blob.exists():
            return HTMLResponse(f"<h2>File '{decoded_filename}' not found in the bucket.</h2>")

        file_stream = blob.open("rb")
        headers = {"Content-Disposition": f"attachment; filename*=utf-8''{urllib.parse.quote(decoded_filename)}"}
        return StreamingResponse(file_stream, media_type="application/octet-stream", headers=headers)
    except Exception as e:
        return HTMLResponse(f"<h2>Error downloading file: {str(e)}</h2>")