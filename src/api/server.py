import asyncio
import logging
from typing import List, Optional, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.db.client import db_cursor

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="News Scraper API", version="2.0.0")

# enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class NewsItem(BaseModel):
    id: int
    title: str
    url: str
    image: Optional[str] = None
    img: Optional[str] = None
    summary: Optional[str] = None
    date: Optional[Any] = None

class NewsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[NewsItem]

def fetch_news_from_db(page: int, page_size: int):
    """Synchronous DB call to be run in a thread pool."""
    with db_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS count FROM news;")
        total_row = cursor.fetchone()
        total = total_row["count"] if total_row else 0

        offset = (page - 1) * page_size
        cursor.execute(
            """
            SELECT id, title, url, image, img, summary, date
            FROM news
            ORDER BY id ASC
            LIMIT %s OFFSET %s;
            """,
            (page_size, offset),
        )
        rows = cursor.fetchall()
        return total, rows

@app.get("/news", response_model=NewsResponse)
async def get_news(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    try:
        total, rows = await asyncio.to_thread(fetch_news_from_db, page, page_size)
    except Exception as e:
        logger.error(f"Database error: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

    return NewsResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=rows
    )

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    # When running from root as 'python src/api/server.py', ensure src is in path
    import os, sys
    sys.path.append(os.getcwd())
    uvicorn.run("src.api.server:app", host="0.0.0.0", port=8001, reload=True)
