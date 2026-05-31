import logging
from pathlib import Path
from typing import Optional
from app.config import get_settings

logger = logging.getLogger(__name__)

def get_s3_client():
    """Dynamically get S3 client if boto3 is installed and credentials are provided."""
    try:
        import boto3
        from botocore.client import Config
    except ImportError:
        logger.warning("boto3 package not installed. Cloud S3 storage will not be available.")
        return None

    settings = get_settings()
    if not settings.s3_access_key_id or not settings.s3_secret_access_key or not settings.s3_bucket_name:
        return None
        
    client_kwargs = {
        "aws_access_key_id": settings.s3_access_key_id,
        "aws_secret_access_key": settings.s3_secret_access_key,
        "region_name": settings.s3_region,
    }
    if settings.s3_endpoint_url:
        client_kwargs["endpoint_url"] = settings.s3_endpoint_url
        client_kwargs["config"] = Config(signature_version="s3v4", s3={'addressing_style': 'path'})
        
    try:
        return boto3.client("s3", **client_kwargs)
    except Exception as e:
        logger.error("Failed to initialize S3 client: %s", e)
        return None

def upload_file_to_s3(file_bytes: bytes, file_name: str, claim_id: str) -> Optional[str]:
    """Uploads file to S3 or an S3-compatible service (like Supabase Storage) and returns the URL."""
    settings = get_settings()
    s3_client = get_s3_client()
    if not s3_client or not settings.s3_bucket_name:
        return None
        
    try:
        s3_key = f"{claim_id}/{file_name}"
        
        # Determine content type
        mime_type = "application/octet-stream"
        if file_name.endswith(".pdf"):
            mime_type = "application/pdf"
        elif file_name.endswith(".png"):
            mime_type = "image/png"
        elif file_name.endswith(".jpg") or file_name.endswith(".jpeg"):
            mime_type = "image/jpeg"
            
        s3_client.put_object(
            Bucket=settings.s3_bucket_name,
            Key=s3_key,
            Body=file_bytes,
            ContentType=mime_type
        )
        
        if settings.s3_endpoint_url:
            # Convert /s3 endpoint to /object/public endpoint for public GET requests
            base_url = settings.s3_endpoint_url.replace('/s3', '/object/public')
            url = f"{base_url}/{settings.s3_bucket_name}/{s3_key}"
        else:
            url = f"https://{settings.s3_bucket_name}.s3.{settings.s3_region}.amazonaws.com/{s3_key}"
            
        logger.info("Successfully uploaded %s to S3: %s", file_name, url)
        return url
    except Exception as e:
        logger.error("Failed to upload to S3: %s", e)
        return None

async def download_file_bytes(file_path: str) -> bytes:
    """Reads bytes from either local file system or a remote URL."""
    if file_path.startswith("http://") or file_path.startswith("https://"):
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(file_path, timeout=30.0)
            response.raise_for_status()
            return response.content
    else:
        return Path(file_path).read_bytes()
