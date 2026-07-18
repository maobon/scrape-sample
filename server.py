from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Response
import json
from typing import Optional

from db import db_cursor

app = FastAPI()

# enable CORS for all origins (adjust as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/news")
async def get_news(page: Optional[int] = None):
    page_size = 20
    if page is not None and page < 1:
        raise HTTPException(status_code=400, detail="page must be greater than or equal to 1")

    try:
        with db_cursor() as cursor:
            if page is None:
                cursor.execute(
                    """
                    SELECT id, title, url, image, img, summary, date
                    FROM news
                    ORDER BY id ASC;
                    """
                )
            else:
                cursor.execute(
                    """
                    SELECT id, title, url, image, img, summary, date
                    FROM news
                    ORDER BY id ASC
                    LIMIT %s OFFSET %s;
                    """,
                    (page_size, (page - 1) * page_size),
                )
            data = cursor.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    news = [
        {
            "id": item["id"],
            "title": item["title"],
            "url": item["url"],
            "image": item["image"],
            "img": item["img"],
            "summary": item["summary"],
            "date": item["date"],
        }
        for item in data
    ]

    return Response(
        content=json.dumps({"News": news}, ensure_ascii=False, default=str),
        media_type="application/json",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8001)
