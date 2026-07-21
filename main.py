import asyncio
import argparse
import logging
import sys
import os

# Ensure src is in path if running directly from root
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.core.scraper import run_scraper

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

DEFAULT_CONFIG_PATH = "config.json"

async def main():
    parser = argparse.ArgumentParser(description='Scrape a configured section by tag or CSS selector')
    parser.add_argument('--config', help='JSON config file path', default=DEFAULT_CONFIG_PATH)
    parser.add_argument('--url', help='Target URL to scrape')
    parser.add_argument('--selector', help='CSS selector to select article items')
    parser.add_argument('--paginate', help='Auto follow pagination', action='store_true', default=True)
    parser.add_argument('--max-pages', help='Max pages to fetch', type=int, default=10)
    parser.add_argument('--per-page', help='Min articles per page', type=int, default=10)
    parser.add_argument('--limit', help='Max results to collect (0 = all)', type=int, default=0)
    parser.add_argument('--json', help='Save output to JSON file path', default='out.json')
    parser.add_argument('--no-paginate', help='Disable pagination', action='store_true')
    parser.add_argument('--clear-database', help='Replace news table before saving scraped results', action='store_true', default=True)
    parser.add_argument('--append-database', help='Do not clear news table before saving; update/append by URL', action='store_false', dest='clear_database')
    parser.add_argument('--clear-minio', help='Clear MinIO before scraping', action='store_true')
    
    args = parser.parse_args()
    if args.no_paginate:
        args.paginate = False

    os.environ["CONFIG_PATH"] = args.config
    await run_scraper(args)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
