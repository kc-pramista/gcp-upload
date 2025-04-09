import os
import mimetypes
import urllib.parse
import uuid, asyncio
from pathlib import Path
from fastapi import FastAPI, Form, UploadFile, File, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from google.cloud import storage
from google.api_core.exceptions import Conflict, NotFound
from fastapi.templating import Jinja2Templates

app = FastAPI()
task_status = {}
task_lock = asyncio.Lock()

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

async def upload_directory_task(task_id: str, directory_path: str):
    try:
        async with task_lock:
            task_status[task_id] = {
                "status": "processing", 
                "files": [],
                "directory_path": directory_path}
        
        if not os.path.isdir(directory_path):
            raise ValueError(f"'{directory_path}' is not a valid directory")

        directory_name = os.path.basename(directory_path.rstrip("/"))
        if not directory_name:
            raise ValueError("Directory path must not be root ('/')")

        for root, _, files in os.walk(directory_path):
            for name in files:
                file_path = os.path.join(root, name)
                file_type, _ = mimetypes.guess_type(file_path)
                file_type = file_type or "application/octet-stream"

                try:
                    # Open the file and upload it to GCS with the directory_name as prefix
                    with open(file_path, "rb") as file_obj:
                        # Construct blob_name with directory_name as prefix
                        relative_path = os.path.relpath(file_path, directory_path)
                        blob_name = f"{directory_name}/{relative_path}" if relative_path != "." else f"{directory_name}/{name}"
                        _upload_file_to_gcs(blob_name, file_obj, file_type)

                    async with task_lock:
                        task_status[task_id]["files"].append({
                            "filename": name,
                            "file_type": file_type,
                            "status": "uploaded",
                            "gcs_path": blob_name
                        })
                except Exception as e:
                    async with task_lock:
                        task_status[task_id]["files"].append({
                            "filename": name,
                            "file_type": file_type,
                            "status": f"failed: {str(e)}",
                            "gcs_path": None
                        })

        async with task_lock:
            task_status[task_id]["status"] = "completed"
    except Exception as e:
        async with task_lock:
            task_status[task_id]["status"] = f"failed: {str(e)}"

@app.get("/tasks")
async def list_all_tasks():
    async with task_lock:
        return task_status

@app.get("/task-status/{task_id}")
async def get_task_status(task_id: str):
    async with task_lock:
        if task_id not in task_status:
            return {"error": "Invalid task ID"}
        return task_status[task_id]

@app.get("/task-status", response_class=HTMLResponse)
async def task_status_page(request: Request):
    return templates.TemplateResponse("task_status.html", {"request": request})


@app.post("/upload-directory")
async def upload_directory(background_tasks: BackgroundTasks, directory_path: str = Form(...)):
    task_id = str(uuid.uuid4())
    async with task_lock:
        task_status[task_id] = {
            "status": "pending", 
            "files": [],
            "directory_path": directory_path}
    
    background_tasks.add_task(upload_directory_task, task_id, directory_path)
    return {"message": "Upload started", "task_id": task_id}

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
async def download_file(filename: str):
    try:
        decoded_filename = urllib.parse.unquote(filename)
        current_bucket = storage_client.bucket(BUCKET_NAME)
        blob = current_bucket.blob(decoded_filename)

        if not blob.exists():
            return HTMLResponse(f"<h2>File '{decoded_filename}' not found in the bucket.</h2>")

        file_stream = blob.open("rb")
        headers = {"Content-Disposition": f"attachment; filename*=utf-8''{urllib.parse.quote(decoded_filename)}"}
        return StreamingResponse(file_stream, media_type="application/octet-stream", headers=headers)
    except Exception as e:
        return HTMLResponse(f"<h2>Error downloading file: {str(e)}</h2>")