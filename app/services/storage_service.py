"""
S3/Storage: client, object keys, signed URLs, thumbnails, upload/delete.
No Flask dependency; callers pass config or use app.config.
"""
import io
import os
import time

from PIL import Image

try:
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:
    boto3 = None
    BotoConfig = None

from app import config as app_config

RESAMPLE_LANCZOS = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS
EXIF_ORIENTATION_TAG = 274

_s3_client = None
_s3_signed_url_cache = {}

PANORAMA_THUMB_MAX_DIMENSION = 400
PANORAMA_THUMB_JPEG_QUALITY = 82
MAX_PANORAMA_THUMB_READ_BYTES = int(os.environ.get('MAX_PANORAMA_THUMB_READ_BYTES', str(20 * 1024 * 1024)))  # 20MB
MAX_PANORAMA_OPTIMIZED_READ_BYTES = int(os.environ.get('MAX_PANORAMA_OPTIMIZED_READ_BYTES', str(50 * 1024 * 1024)))  # 50MB
MARKER_IMAGE_MAX_DIMENSION = 1200
MARKER_IMAGE_THUMB_SIZE = 200
MARKER_IMAGE_JPEG_QUALITY = 85


def use_s3():
    return bool(app_config.SUPABASE_S3_BUCKET)


def get_s3_client():
    global _s3_client
    if not use_s3():
        return None
    if not boto3:
        return None
    if not app_config.SUPABASE_S3_ENDPOINT or not app_config.SUPABASE_S3_REGION:
        return None
    if not app_config.SUPABASE_S3_BUCKET:
        return None
    if not app_config.SUPABASE_S3_ACCESS_KEY_ID or not app_config.SUPABASE_S3_SECRET_ACCESS_KEY:
        return None
    if _s3_client is None:
        kwargs = {
            'service_name': 's3',
            'endpoint_url': app_config.SUPABASE_S3_ENDPOINT,
            'region_name': app_config.SUPABASE_S3_REGION,
            'aws_access_key_id': app_config.SUPABASE_S3_ACCESS_KEY_ID,
            'aws_secret_access_key': app_config.SUPABASE_S3_SECRET_ACCESS_KEY,
        }
        if BotoConfig:
            kwargs['config'] = BotoConfig(
                signature_version='s3v4',
                s3={'addressing_style': 'path'},
                connect_timeout=5,
                read_timeout=30,
                retries={'max_attempts': 6, 'mode': 'standard'},
            )
        _s3_client = boto3.client(**kwargs)
    return _s3_client


def panorama_object_key(filename):
    safe_name = os.path.basename(filename or '').strip()
    if app_config.SUPABASE_S3_PANORAMA_PREFIX:
        return f"{app_config.SUPABASE_S3_PANORAMA_PREFIX}/{safe_name}"
    return safe_name


def panorama_thumb_object_key(filename):
    base = os.path.basename(filename or '').strip()
    if not base:
        return None
    name, ext = os.path.splitext(base)
    thumb_name = f"{name}_thumb.jpg"
    if app_config.SUPABASE_S3_PANORAMA_PREFIX:
        return f"{app_config.SUPABASE_S3_PANORAMA_PREFIX}/{thumb_name}"
    return thumb_name


def generate_panorama_thumb_bytes(raw_bytes):
    if not raw_bytes or len(raw_bytes) > MAX_PANORAMA_THUMB_READ_BYTES:
        return None
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = img.convert('RGB')
    except Exception:
        return None
    w, h = img.size
    if w == 0 or h == 0:
        return None
    if max(w, h) <= PANORAMA_THUMB_MAX_DIMENSION:
        tw, th = w, h
    else:
        ratio = PANORAMA_THUMB_MAX_DIMENSION / float(max(w, h))
        tw = max(1, int(w * ratio))
        th = max(1, int(h * ratio))
    img_thumb = img.resize((tw, th), RESAMPLE_LANCZOS)
    buf = io.BytesIO()
    try:
        img_thumb.save(buf, 'JPEG', quality=PANORAMA_THUMB_JPEG_QUALITY, optimize=True, progressive=True)
    except Exception:
        return None
    return buf.getvalue()


def panorama_optimized_object_key(filename):
    base = os.path.basename(filename or '').strip()
    if not base:
        return None
    name, _ext = os.path.splitext(base)
    opt_name = f"{name}_optimized.jpg"
    if app_config.SUPABASE_S3_PANORAMA_PREFIX:
        return f"{app_config.SUPABASE_S3_PANORAMA_PREFIX}/{opt_name}"
    return opt_name


def generate_panorama_optimized_bytes(raw_bytes):
    if not raw_bytes or len(raw_bytes) > MAX_PANORAMA_OPTIMIZED_READ_BYTES:
        return None
    try:
        img = Image.open(io.BytesIO(raw_bytes))
    except Exception:
        return None
    w, h = img.size
    if w == 0 or h == 0:
        return None
    try:
        exif = img.getexif()
        orientation = exif.get(EXIF_ORIENTATION_TAG)
        if orientation == 3:
            img = img.rotate(180, expand=True)
        elif orientation == 6:
            img = img.rotate(270, expand=True)
        elif orientation == 8:
            img = img.rotate(90, expand=True)
    except Exception:
        pass
    img = img.convert('RGB')
    buf = io.BytesIO()
    try:
        img.save(buf, 'JPEG', quality='keep', optimize=True, progressive=True)
    except Exception:
        try:
            buf = io.BytesIO()
            img.save(buf, 'JPEG', quality=95, optimize=True, progressive=True, subsampling='keep')
        except Exception:
            try:
                buf = io.BytesIO()
                img.save(buf, 'JPEG', quality=95, optimize=True, progressive=True)
            except Exception:
                return None
    result = buf.getvalue()
    if len(result) >= len(raw_bytes):
        return None
    return result


def upload_panorama_optimized_to_s3(filename, opt_bytes):
    if not opt_bytes:
        return
    client = get_s3_client()
    if not client:
        return
    key = panorama_optimized_object_key(filename)
    if not key:
        return
    try:
        client.put_object(
            Bucket=app_config.SUPABASE_S3_BUCKET,
            Key=key,
            Body=opt_bytes,
            ContentType='image/jpeg',
        )
    except Exception:
        pass


def get_panorama_optimized_s3_url(filename):
    client = get_s3_client()
    if not client or not filename:
        return None
    key = panorama_optimized_object_key(filename)
    if not key:
        return None
    now = time.time()
    cached = _s3_signed_url_cache.get(key)
    if cached:
        url, expires_at = cached
        if url and expires_at and now < (expires_at - 30):
            return url
    try:
        client.head_object(Bucket=app_config.SUPABASE_S3_BUCKET, Key=key)
    except Exception:
        return None
    url = client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': app_config.SUPABASE_S3_BUCKET, 'Key': key},
        ExpiresIn=app_config.SUPABASE_S3_SIGNED_URL_TTL,
    )
    try:
        _s3_signed_url_cache[key] = (url, now + float(app_config.SUPABASE_S3_SIGNED_URL_TTL))
    except Exception:
        pass
    return url


def upload_panorama_thumb_to_s3(filename, thumb_bytes):
    if not thumb_bytes:
        return
    client = get_s3_client()
    if not client:
        return
    key = panorama_thumb_object_key(filename)
    if not key:
        return
    try:
        client.put_object(
            Bucket=app_config.SUPABASE_S3_BUCKET,
            Key=key,
            Body=thumb_bytes,
            ContentType='image/jpeg',
        )
    except Exception:
        pass


def get_panorama_thumb_s3_url(filename):
    client = get_s3_client()
    if not client or not filename:
        return None
    key = panorama_thumb_object_key(filename)
    if not key:
        return None
    try:
        client.head_object(Bucket=app_config.SUPABASE_S3_BUCKET, Key=key)
    except Exception:
        return None
    return client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': app_config.SUPABASE_S3_BUCKET, 'Key': key},
        ExpiresIn=app_config.SUPABASE_S3_SIGNED_URL_TTL,
    )


def plot_object_key(filename):
    safe_name = os.path.basename(filename or '').strip()
    if app_config.SUPABASE_S3_PLOT_PREFIX:
        return f"{app_config.SUPABASE_S3_PLOT_PREFIX}/{safe_name}"
    return safe_name


def marker_object_key(filename):
    safe_name = os.path.basename(filename or '').strip()
    if app_config.SUPABASE_S3_MARKER_PREFIX:
        return f"{app_config.SUPABASE_S3_MARKER_PREFIX}/{safe_name}"
    return safe_name


def marker_thumb_object_key(filename):
    base = os.path.basename(filename or '').strip()
    if not base:
        return None
    name, ext = os.path.splitext(base)
    thumb_name = f"{name}_thumb.jpg"
    if app_config.SUPABASE_S3_MARKER_PREFIX:
        return f"{app_config.SUPABASE_S3_MARKER_PREFIX}/{thumb_name}"
    return thumb_name


def compress_marker_image(raw_bytes):
    if not raw_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = img.convert('RGB')
    except Exception:
        return None
    w, h = img.size
    if w == 0 or h == 0:
        return None
    if max(w, h) > MARKER_IMAGE_MAX_DIMENSION:
        ratio = MARKER_IMAGE_MAX_DIMENSION / float(max(w, h))
        new_w = max(1, int(w * ratio))
        new_h = max(1, int(h * ratio))
        img_full = img.resize((new_w, new_h), RESAMPLE_LANCZOS)
    else:
        img_full = img
    if max(img_full.size) > MARKER_IMAGE_THUMB_SIZE:
        ratio = MARKER_IMAGE_THUMB_SIZE / float(max(img_full.size))
        tw = max(1, int(img_full.size[0] * ratio))
        th = max(1, int(img_full.size[1] * ratio))
        img_thumb = img_full.resize((tw, th), RESAMPLE_LANCZOS)
    else:
        img_thumb = img_full
    buf_full = io.BytesIO()
    buf_thumb = io.BytesIO()
    try:
        img_full.save(buf_full, 'JPEG', quality=MARKER_IMAGE_JPEG_QUALITY, optimize=True)
        img_thumb.save(buf_thumb, 'JPEG', quality=MARKER_IMAGE_JPEG_QUALITY, optimize=True)
    except Exception:
        return None
    return buf_full.getvalue(), buf_thumb.getvalue()


def upload_panorama_to_s3(filename, raw_bytes, content_type):
    client = get_s3_client()
    if not client:
        missing = []
        if not boto3:
            missing.append('boto3')
        if not app_config.SUPABASE_S3_ENDPOINT:
            missing.append('SUPABASE_S3_ENDPOINT')
        if not app_config.SUPABASE_S3_BUCKET:
            missing.append('SUPABASE_S3_BUCKET')
        hint = f" Missing: {', '.join(missing)}" if missing else ''
        raise RuntimeError(f'Supabase S3 is not fully configured.{hint}')
    key = panorama_object_key(filename)
    if hasattr(raw_bytes, 'read'):
        try:
            raw_bytes.seek(0)
        except Exception:
            pass
        if hasattr(client, 'upload_fileobj'):
            client.upload_fileobj(
                raw_bytes,
                app_config.SUPABASE_S3_BUCKET,
                key,
                ExtraArgs={'ContentType': content_type},
            )
            return
    client.put_object(
        Bucket=app_config.SUPABASE_S3_BUCKET,
        Key=key,
        Body=raw_bytes,
        ContentType=content_type,
    )


def read_uploaded_file_bytes(file_storage, max_bytes):
    if not file_storage:
        return b''
    stream = getattr(file_storage, 'stream', None) or file_storage
    try:
        stream.seek(0)
    except Exception:
        pass
    cap = int(max_bytes) if max_bytes else 0
    if cap > 0:
        data = stream.read(cap + 1)
        if data and len(data) > cap:
            raise ValueError('file_too_large')
        return data or b''
    return stream.read() or b''


def get_panorama_s3_url(filename):
    client = get_s3_client()
    if not client:
        return None
    key = panorama_object_key(filename)
    now = time.time()
    cached = _s3_signed_url_cache.get(key)
    if cached:
        url, expires_at = cached
        if url and expires_at and now < (expires_at - 30):
            return url
    try:
        client.head_object(Bucket=app_config.SUPABASE_S3_BUCKET, Key=key)
    except Exception:
        return None
    url = client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': app_config.SUPABASE_S3_BUCKET, 'Key': key},
        ExpiresIn=app_config.SUPABASE_S3_SIGNED_URL_TTL,
    )
    try:
        _s3_signed_url_cache[key] = (url, now + float(app_config.SUPABASE_S3_SIGNED_URL_TTL))
    except Exception:
        pass
    return url


def delete_panorama_from_s3(filename):
    client = get_s3_client()
    if not client:
        return
    try:
        client.delete_object(Bucket=app_config.SUPABASE_S3_BUCKET, Key=panorama_object_key(filename))
    except Exception:
        pass


def get_plot_s3_url(filename):
    client = get_s3_client()
    if not client or not filename:
        return None
    key = plot_object_key(filename)
    try:
        client.head_object(Bucket=app_config.SUPABASE_S3_BUCKET, Key=key)
    except Exception:
        return None
    return client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': app_config.SUPABASE_S3_BUCKET, 'Key': key},
        ExpiresIn=app_config.SUPABASE_S3_SIGNED_URL_TTL,
    )


def get_marker_s3_url(filename):
    client = get_s3_client()
    if not client or not filename:
        return None
    key = marker_object_key(filename)
    try:
        client.head_object(Bucket=app_config.SUPABASE_S3_BUCKET, Key=key)
    except Exception:
        return None
    return client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': app_config.SUPABASE_S3_BUCKET, 'Key': key},
        ExpiresIn=app_config.SUPABASE_S3_SIGNED_URL_TTL,
    )


def delete_plot_from_s3(filename):
    client = get_s3_client()
    if not client or not filename:
        return
    try:
        client.delete_object(Bucket=app_config.SUPABASE_S3_BUCKET, Key=plot_object_key(filename))
    except Exception:
        pass


def delete_marker_from_s3(filename):
    client = get_s3_client()
    if not client or not filename:
        return
    try:
        client.delete_object(Bucket=app_config.SUPABASE_S3_BUCKET, Key=marker_object_key(filename))
    except Exception:
        pass


def voiceover_object_key(filename):
    safe_name = os.path.basename(filename or '').strip()
    if app_config.SUPABASE_S3_VOICEOVER_PREFIX:
        return f"{app_config.SUPABASE_S3_VOICEOVER_PREFIX}/{safe_name}"
    return safe_name


def get_voiceover_s3_url(filename):
    client = get_s3_client()
    if not client or not filename:
        return None
    key = voiceover_object_key(filename)
    try:
        client.head_object(Bucket=app_config.SUPABASE_S3_BUCKET, Key=key)
    except Exception:
        return None
    return client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': app_config.SUPABASE_S3_BUCKET, 'Key': key},
        ExpiresIn=app_config.SUPABASE_S3_SIGNED_URL_TTL,
    )


def delete_voiceover_from_s3(filename):
    client = get_s3_client()
    if not client or not filename:
        return
    try:
        client.delete_object(Bucket=app_config.SUPABASE_S3_BUCKET, Key=voiceover_object_key(filename))
    except Exception:
        pass


def probe_image_dimensions(stream):
    """Return (width, height) without full decode."""
    try:
        stream.seek(0)
    except Exception:
        pass
    with Image.open(stream) as img:
        width, height = img.size
        try:
            exif = img.getexif()
            orientation = exif.get(EXIF_ORIENTATION_TAG)
            if orientation in (5, 6, 7, 8):
                width, height = height, width
        except Exception:
            pass
    return int(width or 0), int(height or 0)
