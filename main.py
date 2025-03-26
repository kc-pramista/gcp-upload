from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.responses import StreamingResponse
from google.cloud import storage
from google.api_core.exceptions import Conflict, NotFound
from datetime import timedelta
import urllib.parse
from fastapi.responses import RedirectResponse

app = FastAPI()

# Google Cloud Storage setup
BUCKET_NAME = "mybucket_trialkc"
storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)

@app.post("/set-active-bucket")
async def set_active_bucket(bucket_name: str = Form(...)):
    global BUCKET_NAME
    BUCKET_NAME = bucket_name  # Update BUCKET_NAME with the selected bucket
    return RedirectResponse(url="/", status_code=303)

# read html files that are stored differently
def load_html(filename):
    with open(f"templates/{filename}", "r", encoding="utf-8") as file:
        return file.read()

@app.get("/", response_class=HTMLResponse)
async def main_page():
    return load_html("index.html").replace("{{BUCKET_NAME}}", BUCKET_NAME)


#fast api file uploading 
# Update current bucket
@app.get("/select-active-bucket", response_class=HTMLResponse)
async def select_active_bucket():
    # List all buckets in the project
    buckets = storage_client.list_buckets()
    
    bucket_list_html = ""
    for bucket in buckets:
        bucket_list_html += f'<option value="{bucket.name}">{bucket.name}</option>'
    
    # Provide the form for selecting an active bucket
    return load_html("select_active_bucket.html").replace("{{BUCKET_OPTIONS}}", bucket_list_html)


#BUCKETS

#create bucket html and gcp edits
@app.get("/create-bucket-form", response_class=HTMLResponse)
async def create_bucket_form():
    return load_html("create_bucket.html")

@app.post("/create-bucket")
async def create_bucket(bucket_name: str = Form(...)):
    try:
        existing_bucket = storage_client.get_bucket(bucket_name)
        if existing_bucket:
            return f"<h2>Bucket '{bucket_name}' already exists.</h2>"

    except NotFound:
        # Create the bucket if it does not exist
        new_bucket = storage_client.create_bucket(bucket_name)
        return f"<h2>Bucket '{new_bucket.name}' created successfully.</h2>"

    except Exception as e:
        return f"<h2>Error creating bucket: {str(e)}</h2>"


#list buckets
@app.get("/list-buckets", response_class=HTMLResponse)
async def list_buckets():
    try:
        # List all the buckets in the project
        buckets = storage_client.list_buckets()
        
        if not buckets:
            return "<h2>No buckets found in the active project.</h2>"

        # Generate HTML for bucket list
        bucket_list_html = ""
        for bucket in buckets:
            bucket_list_html += f'<li>{bucket.name}</li>'

        # Load the HTML template and inject the bucket list
        template = load_html("list_buckets.html")
        return template.replace("{{BUCKETS}}", bucket_list_html)

    except Exception as e:
        return f"<h2>Error retrieving buckets: {str(e)}</h2>"


#delete bucket html & gcp
@app.get("/delete-bucket-form", response_class=HTMLResponse)
async def delete_bucket_form():
    return load_html("delete_bucket.html")

@app.post("/delete-bucket")
async def delete_bucket(bucket_name: str = Form(...)):
    try:
        bucket_to_delete = storage_client.get_bucket(bucket_name)
        bucket_to_delete.delete()
        return f"<h2>Bucket '{bucket_name}' deleted successfully.</h2>"
    except NotFound:
        return f"<h2>Bucket '{bucket_name}' not found.</h2>"
    except Exception as e:
        return f"<h2>Error deleting bucket: {str(e)}</h2>"




















#upload html & gcp
@app.get("/upload-form", response_class=HTMLResponse)
async def upload_form():
    return load_html("upload.html")


#100 mb - 50 sec
#255 mb - 1:50 min
#1 gb - 4:30 min
@app.post("/upload")
async def upload_large_file(file: UploadFile = File(...)):
    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(file.filename)

    chunk_size = 10 * 1024 * 1024  # 10 mb chunk
    chunk_size = 10 * 1024 * 1024  # 10 MB chunk
    with file.file as f:
        chunk = f.read(chunk_size)
        while chunk:
            blob.upload_from_string(chunk)
            chunk = f.read(chunk_size)  # Read the next chunk


    return {"message": f"File '{file.filename}' uploaded successfully in chunks."}


#100 mb file - 1 minute
#255 mb - 1.30 minute
#1 gb - 4 minutes

# @app.post("/upload")
# async def upload_large_file(file: UploadFile = File(...)):
#     storage_client = storage.Client()
#     bucket = storage_client.bucket(BUCKET_NAME)
#     blob = bucket.blob(file.filename)

#     # Open the file and stream it directly to Google Cloud Storage
#     with file.file as f:
#         blob.upload_from_file(f, content_type=file.content_type)

#     return {"message": f"File uploaded: gs://your-bucket-name/{file.filename}"}


# took 10 mins to upload 100 mb file with upload internet speed of 5 mbps
# @app.post("/upload")
# async def upload_large_file(file: UploadFile = File(...)):
#     try:
#         blob = bucket.blob(file.filename)

#         resumable_url = blob.create_resumable_upload_session(content_type=file.content_type)

#         with file.file as file_obj:
#             blob.upload_from_file(
#                 file_obj,
#                 content_type=file.content_type,
#                 rewind=True,
#                 timeout=900  
#             )

#         return {"message": f"File '{file.filename}' uploaded successfully to bucket '{BUCKET_NAME}'"}

#     except Exception as e:
#         return {"error": f"Error uploading file: {str(e)}"}





def safe_filename(filename: str) -> str:
    return urllib.parse.quote(filename)

@app.get("/retrieve-files", response_class=HTMLResponse)
async def retrieve_files():
    try:
        #all blobs in the bucket
        blobs = list(bucket.list_blobs())
        
        if not blobs:
            return "<h2>No files found in the active bucket.</h2>"

        file_list_html = "<h2>Files in Active Bucket</h2><ul>"
        for blob in blobs:
            #file name encoding because some files may contain space and special characters
            encoded_filename = safe_filename(blob.name)
            file_list_html += f'<li>{blob.name} <a href="/download-file/{encoded_filename}"><img src="https://img.icons8.com/ios/50/000000/download.png" alt="Download" width="15" height="15" /></a></li>'

        file_list_html += "</ul>"

        return load_html("retrieve_files.html").replace("{{FILES}}", file_list_html)

    except Exception as e:
        return f"<h2>Error retrieving files: {str(e)}</h2>"

@app.get("/download-file/{filename}")
async def download_file(filename: str):
    try:
        #decode the file name to its original so it stays the same
        decoded_filename = urllib.parse.unquote(filename)
        blob = bucket.blob(decoded_filename)

        if not blob.exists():
            return f"<h2>File '{decoded_filename}' not found in the bucket.</h2>"

        #downloading
        file_stream = blob.open("rb")
        
        # utf-8 because i was getting error while downloading file with -
        headers = {"Content-Disposition": f"attachment; filename*=utf-8''{urllib.parse.quote(decoded_filename)}"}
        
        return StreamingResponse(file_stream, media_type="application/octet-stream", headers=headers)
    
    except Exception as e:
        return f"<h2>Error downloading file: {str(e)}</h2>"