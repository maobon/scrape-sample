import asyncio
import hashlib
import logging
from pathlib import Path
import httpx
from urllib.parse import urlparse

from src.utils.minio import upload_image

logger = logging.getLogger(__name__)

def get_image_extension(image_url):
    suffix = Path(urlparse(image_url).path).suffix.lower()
    if suffix == '.jpeg':
        return '.jpg'
    if suffix in {'.jpg', '.png'}:
        return suffix
    return ''

def get_image_filename(image_url):
    if not image_url:
        return None
    image_hash = hashlib.sha256(image_url.encode('utf-8')).hexdigest()
    return f'{image_hash}{get_image_extension(image_url)}'

async def download_and_upload_image(client, image_url, output_dir, semaphore):
    if not image_url:
        return {'url': None, 'filename': None, 'status': 'skipped'}

    image_filename = get_image_filename(image_url)
    image_path = Path(output_dir) / image_filename
    result = {'url': image_url, 'filename': image_filename, 'status': 'failed'}

    async with semaphore:
        if image_path.exists() and image_path.stat().st_size > 0:
            try:
                await asyncio.to_thread(upload_image, image_path, image_filename, verbose=True)
                result['status'] = 'skipped'
                return result
            except Exception as e:
                logger.error(f"Failed to upload existing image {image_filename}: {e}")
                result['status'] = 'upload_failed'
                return result

        try:
            response = await client.get(image_url, timeout=20.0, follow_redirects=True)
            response.raise_for_status()
            
            image_path.parent.mkdir(parents=True, exist_ok=True)
            with open(image_path, 'wb') as f:
                f.write(response.content)
            result['status'] = 'downloaded'
        except Exception as e:
            logger.error(f"Failed to download image {image_url}: {e}")
            if image_path.exists():
                image_path.unlink()
            result['status'] = 'failed'
            return result

        try:
            await asyncio.to_thread(upload_image, image_path, image_filename, verbose=True)
        except Exception as e:
            logger.error(f"Failed to upload image {image_filename}: {e}")
            result['status'] = 'upload_failed'
            
    return result

async def download_images_async(records, output_dir='images', max_concurrent=10):
    image_dir = Path(output_dir)
    image_dir.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(max_concurrent)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    stats = {'downloaded': 0, 'skipped': 0, 'failed': 0, 'upload_failed': 0}
    url_to_filename = {}

    async with httpx.AsyncClient(headers=headers, verify=False) as client:
        tasks = []
        for record in records:
            image_url = record.get('image')
            if image_url:
                tasks.append(download_and_upload_image(client, image_url, output_dir, semaphore))
        
        if not tasks:
            return stats, url_to_filename

        results = await asyncio.gather(*tasks)
        for res in results:
            status = res['status']
            stats[status] = stats.get(status, 0) + 1
            if status in ('downloaded', 'skipped'):
                url_to_filename[res['url']] = res['filename']

    return stats, url_to_filename
