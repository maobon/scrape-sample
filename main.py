from urllib.parse import urljoin, urlparse
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import requests
from bs4 import BeautifulSoup
from utils import get_local_ip

DEFAULT_CONFIG_PATH = "config.json"

parser = argparse.ArgumentParser(description='Scrape a configured section by tag or CSS selector')
parser.add_argument('--config', help='JSON config file path', default=DEFAULT_CONFIG_PATH)
parser.add_argument('--url', help='Target URL to scrape (overrides config and SCRAPER_TARGET_URL)')
parser.add_argument('--tag', help='Label text to locate (fallback)', default='NEWS ANALYSIS')
parser.add_argument('--selector', help='CSS selector to select article items (overrides config)')
# paginate default set to True
parser.add_argument('--paginate', help='Auto follow pagination to collect multiple pages (uses ?page=N)', action='store_true', default=True)
parser.add_argument('--max-pages', help='Maximum number of pages to fetch when paginating', type=int, default=10)
parser.add_argument(
    '--per-page',
    help='Minimum number of strict articles to collect before trying relaxed extraction',
    type=int,
    default=10,
)
parser.add_argument('--limit', help='Max number of results to print (0 = all)', type=int, default=0)
parser.add_argument('--json', help='Save output to JSON file path', default='out.json')
parser.add_argument('--no-paginate', help='Disable automatic pagination', action='store_true')
parser.add_argument('--render-dates', help='Use Playwright to render article pages and extract publish dates', action='store_true', default=False)
args = parser.parse_args()

# respect --no-paginate
if args.no_paginate:
    args.paginate = False

os.environ["CONFIG_PATH"] = args.config


def load_config(config_path):
    path = Path(config_path)
    if not path.exists():
        return {}

    with path.open('r', encoding='utf-8') as f:
        config = json.load(f)

    if not isinstance(config, dict):
        raise ValueError(f'Config root must be a JSON object: {config_path}')

    return config


def resolve_target_config():
    config = load_config(args.config)
    target_url = args.url or os.getenv('SCRAPER_TARGET_URL') or config.get('target_url')
    if not target_url:
        parser.error(
            'Missing target URL. Set "target_url" in config.json, '
            'set SCRAPER_TARGET_URL, or pass --url.'
        )

    selector = args.selector or config.get('selector')
    fallback_selectors = config.get('fallback_selectors') or ['article', 'li']
    if isinstance(fallback_selectors, str):
        fallback_selectors = [fallback_selectors]
    if not isinstance(fallback_selectors, list) or not all(isinstance(item, str) for item in fallback_selectors):
        raise ValueError('Config "fallback_selectors" must be a string or list of strings')

    title_blacklist = config.get('title_blacklist') or []
    if isinstance(title_blacklist, str):
        title_blacklist = [title_blacklist]
    if not isinstance(title_blacklist, list) or not all(isinstance(item, str) for item in title_blacklist):
        raise ValueError('Config "title_blacklist" must be a string or list of strings')

    return target_url, selector, fallback_selectors, title_blacklist


TARGET_URL, ITEM_SELECTOR, FALLBACK_SELECTORS, TITLE_BLACKLIST = resolve_target_config()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_soup(url):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, 'html.parser')


def clear_output_state(json_path='out.json', image_dir='images'):
    Path(json_path).unlink(missing_ok=True)

    image_path = Path(image_dir)
    image_path.mkdir(parents=True, exist_ok=True)
    deleted_images = 0
    for path in image_path.iterdir():
        if path.is_file() or path.is_symlink():
            path.unlink()
            deleted_images += 1

    try:
        from db import clear_news_table

        clear_news_table()
    except Exception as e:
        print(f'Failed to clear PostgreSQL table news: {e}', file=sys.stderr)
    else:
        print('Cleared PostgreSQL table news')

    try:
        from minio_client import clear_bucket

        deleted_minio_images = clear_bucket(verbose=True)
    except Exception as e:
        print(f'Failed to clear MinIO bucket img: {e}', file=sys.stderr)
    else:
        print(f'Cleared MinIO bucket img: {deleted_minio_images} objects')

    print(f'Cleared JSON: {json_path}')
    print(f'Cleared images: {deleted_images} files')


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


def build_img_url(image_url, base_url):
    image_filename = get_image_filename(image_url)
    if not image_filename:
        return ''

    return f'{base_url}/img/{image_filename}'


def add_local_img_urls(items):
    base_url = f'http://{get_local_ip()}:9000'
    for item in items:
        item['img'] = build_img_url(item.get('image'), base_url)


def download_images_from_json(json_path, output_dir='images'):
    """Download every image URL in json_path into output_dir.

    The output file name is the sha256 hash of the image field value,
    plus the image extension from the URL when it is .jpg or .png.
    """
    path = Path(json_path)
    if not path.exists():
        print(f'Image download skipped: JSON file does not exist: {json_path}', file=sys.stderr)
        return {'downloaded': 0, 'skipped': 0, 'failed': 0}

    with path.open('r', encoding='utf-8') as f:
        records = json.load(f)

    if not isinstance(records, list):
        print(f'Image download skipped: JSON root is not a list: {json_path}', file=sys.stderr)
        return {'downloaded': 0, 'skipped': 0, 'failed': 0}

    image_dir = Path(output_dir)
    image_dir.mkdir(parents=True, exist_ok=True)

    try:
        from minio_client import upload_image
    except Exception as e:
        upload_image = None
        print(f'MinIO upload disabled: {e}', file=sys.stderr)

    stats = {'downloaded': 0, 'skipped': 0, 'failed': 0, 'uploaded': 0, 'upload_failed': 0}
    for idx, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            stats['skipped'] += 1
            continue

        image_url = record.get('image')
        if not image_url:
            stats['skipped'] += 1
            continue

        image_filename = get_image_filename(image_url)
        image_path = image_dir / image_filename
        legacy_image_path = image_dir / hashlib.sha256(image_url.encode('utf-8')).hexdigest()
        if legacy_image_path.exists() and not image_path.exists():
            legacy_image_path.rename(image_path)

        if image_path.exists() and image_path.stat().st_size > 0:
            if upload_image:
                try:
                    upload_image(image_path, image_filename, verbose=True)
                    stats['uploaded'] += 1
                except Exception as e:
                    stats['upload_failed'] += 1
                    print(f'Failed to upload image #{idx} to MinIO: {image_filename} ({e})', file=sys.stderr)
            stats['skipped'] += 1
            continue

        try:
            with requests.get(image_url, headers=HEADERS, timeout=20, stream=True) as resp:
                resp.raise_for_status()
                with image_path.open('wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
        except Exception as e:
            stats['failed'] += 1
            image_path.unlink(missing_ok=True)
            print(f'Failed to download image #{idx}: {image_url} ({e})', file=sys.stderr)
        else:
            stats['downloaded'] += 1
            if upload_image:
                try:
                    upload_image(image_path, image_filename, verbose=True)
                    stats['uploaded'] += 1
                except Exception as e:
                    stats['upload_failed'] += 1
                    print(f'Failed to upload image #{idx} to MinIO: {image_filename} ({e})', file=sys.stderr)

    return stats


def get_img_src(img):
    if not img:
        return None
    for attr in ('src', 'data-src', 'data-srcset', 'data-original'):
        v = img.get(attr)
        if v:
            return v
    srcset = img.get('srcset')
    if srcset:
        return srcset.split(',')[-1].strip().split(' ')[0]
    return None


URL_BLACKLIST_SUBSTR = []
DATA_TESTID_EXACT = {'live', 'video', 'interactive', 'load-more-posts', 'load-more'}


def is_unwanted_strict(title, href, item):
    """Return True only for high-confidence non-article items.

    Heuristics (in priority):
    1. href contains a disallowed path (/live/, /video/, /interactive/) -> block
    2. element has a data-testid whose value exactly matches known non-article tokens -> block
    3. exact-title matches (small set) -> block

    Avoid using loose class-name or substring matches to reduce false positives.
    """
    if not title:
        return True

    href_l = (href or '').lower()
    # 1) URL-based high-confidence filters
    for sub in URL_BLACKLIST_SUBSTR:
        if sub in href_l:
            return True

    # 2) explicit data-testid tokens
    # check element and its descendants for a data-testid attribute that equals one of the tokens
    for el in item.find_all(attrs={'data-testid': True}):
        val = (el.get('data-testid') or '').strip().lower()
        if val in DATA_TESTID_EXACT:
            return True

    # 3) exact-title phrases
    normalized_title = title.lower().strip().rstrip('.')
    normalized_blacklist = [item.lower().strip().rstrip('.') for item in TITLE_BLACKLIST]
    for sub in normalized_blacklist:
        if sub in normalized_title:
            return True

    return False


import re

def extract_from_item(item, base_url, strict=True):
    # Prefer the anchor that contains the article title (h2/h3).
    title_el = item.select_one('h2, h3')
    a = None
    if title_el:
        a = title_el.find_parent('a')
    if a is None:
        a = item.select_one('a[href]')
    if not a:
        return None

    href = a.get('href')
    if not href:
        return None
    full = urljoin(base_url, href)

    # Reject non-article links: require a year segment like /2026/ in the URL for article pages
    if not re.search(r"/\d{4}/", full):
        return None

    # Title: prefer the header element's text when available
    title = title_el.get_text(' ', strip=True) if title_el else (a.get('aria-label') or a.get('title') or a.get_text(' ', strip=True))
    if not title:
        return None

    href_l = (href or '').lower()
    if any(sub in href_l for sub in URL_BLACKLIST_SUBSTR):
        return None

    normalized_title = title.lower().strip().rstrip('.')
    normalized_blacklist = [item.lower().strip().rstrip('.') for item in TITLE_BLACKLIST]
    if any(sub in normalized_title for sub in normalized_blacklist):
        return None

    if strict and is_unwanted_strict(title, href, item):
        return None

    # Date
    time_el = item.select_one('time')
    date = None
    if time_el:
        date = time_el.get_text(' ', strip=True) or time_el.get('datetime')

    # Summary
    p = item.select_one('p')
    summary = p.get_text(' ', strip=True) if p else None

    # Image
    img = item.select_one('img') or a.select_one('img')
    image = get_img_src(img)

    # Fallback: if no date found on the card, try to parse Y/M/D from the article URL
    if not date:
        m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", full)
        if m:
            y, mo, d = m.groups()
            date = f"{y}-{mo}-{d}"

    return {'title': title, 'url': full, 'image': image, 'summary': summary, 'date': date}



def get_published_from_article(url):
    """Fetch article page and try to extract published datetime via requests."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception:
        return None
    soup = BeautifulSoup(resp.text, 'html.parser')
    # meta tags
    for attr in (('property','article:published'), ('property','article:published_time'), ('name','ptime'), ('name','date')):
        m = soup.find('meta', attrs={attr[0]: attr[1]})
        if m and m.get('content'):
            return m.get('content').strip()
    # time element
    t = soup.select_one('time')
    if t:
        dt = t.get('datetime') or t.get_text(' ', strip=True)
        if dt:
            return dt
    # JSON-LD
    for s in soup.select("script[type='application/ld+json']"):
        try:
            import json as _json
            data = _json.loads(s.string)
            objs = data if isinstance(data, list) else [data]
            for o in objs:
                if isinstance(o, dict) and o.get('datePublished'):
                    return o.get('datePublished')
                if isinstance(o, dict) and '@graph' in o:
                    for g in o['@graph']:
                        if isinstance(g, dict) and g.get('datePublished'):
                            return g.get('datePublished')
        except Exception:
            continue
    return None

clear_output_state(args.json)

results = []
seen_urls = set()


def add_result(data):
    if not data or data['url'] in seen_urls:
        return False

    seen_urls.add(data['url'])
    results.append(data)
    return True


def reached_limit():
    return bool(args.limit and len(results) >= args.limit)


def collect_from_candidates(candidates, page_url):
    page_count = 0

    for el in candidates:
        if reached_limit():
            break
        data = extract_from_item(el, base_url=page_url, strict=True)
        if add_result(data):
            page_count += 1

    if page_count < args.per_page and not reached_limit():
        for el in candidates:
            if reached_limit() or page_count >= args.per_page:
                break
            data = extract_from_item(el, base_url=page_url, strict=False)
            if add_result(data):
                page_count += 1

    return page_count

if args.paginate:
    for page in range(1, args.max_pages + 1):
        if page == 1:
            page_url = TARGET_URL
        else:
            sep = '&' if '?' in TARGET_URL else '?'
            page_url = f"{TARGET_URL}{sep}page={page}"
        try:
            soup = fetch_soup(page_url)
        except Exception as e:
            print(f'Failed to fetch {page_url}: {e}', file=sys.stderr)
            break

        # Build candidate list preserving order
        candidates = []
        added = set()
        precise = soup.select(ITEM_SELECTOR) if ITEM_SELECTOR else []
        for el in precise:
            k = id(el)
            if k not in added:
                candidates.append(el)
                added.add(k)

        fallback_selector = ', '.join(FALLBACK_SELECTORS)
        broader = soup.select(fallback_selector)
        for el in broader:
            k = id(el)
            if k not in added:
                candidates.append(el)
                added.add(k)

        collected = collect_from_candidates(candidates, page_url)

        print(f'Page {page}: collected {collected} items')
        if reached_limit():
            break
        time.sleep(0.5)
else:
    try:
        soup = fetch_soup(TARGET_URL)
    except Exception as e:
        print(f'Failed to fetch {TARGET_URL}: {e}', file=sys.stderr)
        sys.exit(1)
    candidates = soup.select(ITEM_SELECTOR) if ITEM_SELECTOR else []
    if not candidates:
        candidates = soup.select(', '.join(FALLBACK_SELECTORS))
    collect_from_candidates(candidates, TARGET_URL)

# Output and optional save
if args.render_dates:
    # Use Playwright to render article pages and extract publish dates when server blocks direct requests
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print('Playwright not installed or not available; dates will not be rendered. Install with: python3 -m pip install playwright --user && python3 -m playwright install chromium', file=sys.stderr)
    else:
        print('Rendering article pages to extract publish dates (this may take a while)...')
        import json as _json
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=HEADERS.get('User-Agent'))
            for idx, item in enumerate(results):
                # skip if date already present
                if item.get('date'):
                    continue
                url = item.get('url')
                try:
                    resp = page.goto(url, wait_until='domcontentloaded', timeout=20000)
                    # if blocked, skip
                    body = page.content()
                    if 'Please enable JS' in body or 'captcha-delivery' in body or 'Please enable JavaScript' in body or 'captcha' in body.lower():
                        continue

                    # extract published date/time
                    published = None
                    date_selectors = ["meta[property='article:published']", "meta[property='article:published_time']", "meta[name='ptime']", "time[datetime]"]
                    for sel in date_selectors:
                        try:
                            el = page.query_selector(sel)
                        except Exception:
                            el = None
                        if el:
                            if sel.startswith('meta'):
                                v = el.get_attribute('content')
                            else:
                                v = el.get_attribute('datetime') or el.inner_text()
                            if v:
                                published = v.strip()
                                break

                    # try JSON-LD
                    if not published:
                        for s in page.query_selector_all("script[type='application/ld+json']"):
                            try:
                                txt = s.inner_text()
                                data = _json.loads(txt)
                                objs = data if isinstance(data, list) else [data]
                                for o in objs:
                                    if isinstance(o, dict) and o.get('datePublished'):
                                        published = o.get('datePublished')
                                        break
                                    if isinstance(o, dict) and '@graph' in o:
                                        for g in o['@graph']:
                                            if isinstance(g, dict) and g.get('datePublished'):
                                                published = g.get('datePublished')
                                                break
                                    if published:
                                        break
                                if published:
                                    break
                            except Exception:
                                continue

                    if published:
                        item['date'] = published
                except Exception:
                    # ignore failures and continue
                    pass
            try:
                browser.close()
            except Exception:
                pass

add_local_img_urls(results)

try:
    from db import replace_news

    print(f"[DB DEBUG] Preparing to save {len(results)} articles to PostgreSQL...")
    db_result = replace_news(results)
    print("[DB DEBUG] Database operation executed successfully.")
    for item, row in zip(results, db_result["rows"]):
        item["id"] = row["id"]
        print(f"[DB DEBUG] Saved row id={row['id']} url={row['url']}")
    print(f"Saved to PostgreSQL table news: {db_result['count']} rows")
except Exception as e:
    print(f'Failed to save to PostgreSQL: {e}', file=sys.stderr)

if args.json:
    with open(args.json, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'Saved JSON: {args.json}')
    image_stats = download_images_from_json(args.json)
    print(
        'Downloaded images: '
        f"{image_stats['downloaded']} new, "
        f"{image_stats['skipped']} skipped, "
        f"{image_stats['failed']} failed, "
        f"{image_stats['uploaded']} uploaded to MinIO, "
        f"{image_stats['upload_failed']} upload failed"
    )

for i, a in enumerate(results, start=1):
    print(f"{i}. {a['title']}")
    if a.get('date'):
        print(f"   日期：{a['date']}")
    print(f"   链接：{a['url']}")
    if a.get('img'):
        print(f"   本地图片：{a['img']}")
    if a.get('summary'):
        print(f"   摘要：{a['summary']}")
    print(f"   图片：{a['image']}")
    print()

print(f'Total articles: {len(results)}')
