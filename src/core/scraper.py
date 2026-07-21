import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from src.core.extractor import extract_from_item
from src.core.downloader import download_images_async, get_image_filename
from src.db.client import upsert_news, clear_news_table
from src.utils.minio import clear_bucket
from src.utils.config import load_app_config

logger = logging.getLogger(__name__)

def _bool_value(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes"}

def get_base_img_url(config):
    img_config = config.get('img') or {}
    minio_config = config.get('minio') or {}

    configured_base_url = img_config.get('base_url') or os.getenv('IMG_BASE_URL')
    if configured_base_url:
        return str(configured_base_url).rstrip('/')

    host = img_config.get('host') or os.getenv('IMG_HOST')
    if not host:
        raise ValueError('Missing config: img.host is required to build image URLs')

    bucket = img_config.get('bucket') or os.getenv('IMG_BUCKET') or minio_config.get('bucket') or 'img'
    scheme = img_config.get('scheme') or os.getenv('IMG_SCHEME')
    if not scheme:
        secure = _bool_value(img_config.get('secure'), _bool_value(minio_config.get('secure'), False))
        scheme = 'https' if secure else 'http'

    host = str(host).strip().rstrip('/')
    bucket = str(bucket).strip().strip('/')

    if host.startswith(('http://', 'https://')):
        return f"{host}/{bucket}" if bucket else host

    return f"{scheme}://{host}/{bucket}" if bucket else f"{scheme}://{host}"

async def fetch_soup(client, url):
    resp = await client.get(url, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, 'html.parser')

def clear_output_state(args):
    Path(args.json).unlink(missing_ok=True)
    
    image_dir = Path('images')
    image_dir.mkdir(parents=True, exist_ok=True)
    deleted_images = 0
    for path in image_dir.iterdir():
        if path.is_file() or path.is_symlink():
            path.unlink()
            deleted_images += 1

    if args.clear_minio:
        try:
            deleted_minio_images = clear_bucket(verbose=True)
            logger.info(f'Cleared MinIO bucket img: {deleted_minio_images} objects')
        except Exception as e:
            logger.error(f'Failed to clear MinIO bucket img: {e}')

    logger.info(f'Cleared JSON: {args.json}')
    logger.info(f'Cleared local images: {deleted_images} files')

async def run_scraper(args):
    config = load_app_config(args.config)
    target_url = args.url or os.getenv('SCRAPER_TARGET_URL') or config.get('target_url')
    if not target_url:
        logger.error('Missing target URL.')
        return

    item_selector = args.selector or config.get('selector')
    fallback_selectors = config.get('fallback_selectors') or ['article', 'li']
    if isinstance(fallback_selectors, str):
        fallback_selectors = [fallback_selectors]
    
    title_blacklist = config.get('title_blacklist') or []
    if isinstance(title_blacklist, str):
        title_blacklist = [title_blacklist]

    clear_output_state(args)

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    results = []
    seen_urls = set()

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        max_pages = args.max_pages if args.paginate else 1
        for page in range(1, max_pages + 1):
            if page == 1:
                page_url = target_url
            else:
                sep = '&' if '?' in target_url else '?'
                page_url = f"{target_url}{sep}page={page}"
            
            logger.info(f"Fetching page {page}: {page_url}")
            try:
                soup = await fetch_soup(client, page_url)
            except Exception as e:
                logger.error(f'Failed to fetch {page_url}: {e}')
                break

            candidates = []
            added = set()
            precise = soup.select(item_selector) if item_selector else []
            for el in precise:
                if id(el) not in added:
                    candidates.append(el)
                    added.add(id(el))

            fallback_selector = ', '.join(fallback_selectors)
            broader = soup.select(fallback_selector)
            for el in broader:
                if id(el) not in added:
                    candidates.append(el)
                    added.add(id(el))

            page_collected = 0
            for el in candidates:
                if args.limit and len(results) >= args.limit:
                    break
                data = extract_from_item(el, base_url=page_url, strict=True, title_blacklist=title_blacklist)
                if data and data['url'] not in seen_urls:
                    seen_urls.add(data['url'])
                    results.append(data)
                    page_collected += 1

            if page_collected < args.per_page and (not args.limit or len(results) < args.limit):
                for el in candidates:
                    if args.limit and len(results) >= args.limit:
                        break
                    if page_collected >= args.per_page:
                        break
                    data = extract_from_item(el, base_url=page_url, strict=False, title_blacklist=title_blacklist)
                    if data and data['url'] not in seen_urls:
                        seen_urls.add(data['url'])
                        results.append(data)
                        page_collected += 1

            logger.info(f'Page {page}: collected {page_collected} items')
            if args.limit and len(results) >= args.limit:
                break
            
            if args.paginate:
                await asyncio.sleep(0.5)

    if results:
        logger.info(f"Collected {len(results)} articles. Starting image downloads...")
        img_stats, url_map = await download_images_async(results)
        
        logger.info(
            f"Image processing finished: "
            f"{img_stats.get('downloaded', 0)} downloaded, "
            f"{img_stats.get('skipped', 0)} skipped, "
            f"{img_stats.get('failed', 0)} failed, "
            f"{img_stats.get('upload_failed', 0)} upload failed."
        )
        
        base_img_url = get_base_img_url(config)
        for item in results:
            orig_url = item.get('image')
            if orig_url and orig_url in url_map:
                fname = url_map[orig_url]
                item['img'] = f"{base_img_url}/{fname}"
            else:
                item['img'] = ""

        logger.info(f"Saving {len(results)} articles to Database...")
        try:
            if args.clear_database:
                clear_news_table()
                logger.info('Cleared PostgreSQL table news and reset id sequence')

            db_result = upsert_news(results)
            logger.info(f"Database operation successful. Total rows in DB: {db_result['count']}")
        except Exception as e:
            logger.error(f"Failed to save to PostgreSQL: {e}")

        if args.json:
            with open(args.json, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved results to {args.json}")
    else:
        logger.info("No articles collected.")
