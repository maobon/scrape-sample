import re
from urllib.parse import urljoin, urlparse

URL_BLACKLIST_SUBSTR = []
DATA_TESTID_EXACT = {'live', 'interactive', 'load-more-posts', 'load-more'}
ITEM_TEXT_BLACKLIST = {
    'this was featured in live coverage.',
}

def is_supported_story_url(url):
    parsed = urlparse(url)
    return bool(
        re.search(r"/\d{4}/", parsed.path)
        or parsed.path.startswith("/video/")
    )

def is_unwanted_strict(title, href, item, title_blacklist=None):
    if not title:
        return True

    item_text = item.get_text(' ', strip=True).lower()
    for sub in ITEM_TEXT_BLACKLIST:
        if sub in item_text:
            return True

    href_l = (href or '').lower()
    for sub in URL_BLACKLIST_SUBSTR:
        if sub in href_l:
            return True

    for el in item.find_all(attrs={'data-testid': True}):
        val = (el.get('data-testid') or '').strip().lower()
        if val in DATA_TESTID_EXACT:
            return True

    normalized_title = title.lower().strip().rstrip('.')
    if title_blacklist:
        normalized_blacklist = [it.lower().strip().rstrip('.') for it in title_blacklist]
        for sub in normalized_blacklist:
            if sub in normalized_title:
                return True

    return False

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

def extract_from_item(item, base_url, strict=True, title_blacklist=None):
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

    if not is_supported_story_url(full):
        return None

    title = title_el.get_text(' ', strip=True) if title_el else (a.get('aria-label') or a.get('title') or a.get_text(' ', strip=True))
    if not title:
        return None

    if is_unwanted_strict(title, href, item, title_blacklist):
        return None

    time_el = item.select_one('time')
    date = None
    if time_el:
        date = time_el.get_text(' ', strip=True) or time_el.get('datetime')

    p = item.select_one('p')
    summary = p.get_text(' ', strip=True) if p else None

    img = item.select_one('img') or a.select_one('img')
    image = get_img_src(img)

    if not date:
        m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", full)
        if m:
            y, mo, d = m.groups()
            date = f"{y}-{mo}-{d}"

    return {'title': title, 'url': full, 'image': image, 'summary': summary, 'date': date}
