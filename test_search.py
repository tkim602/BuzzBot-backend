import asyncio
import os

# Load API key
for line in open(".env"):
    if "OPENAI_API_KEY=" in line:
        os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1].strip()

from db.session import SyncSessionLocal
from app.rag.retrieval import get_query_embedding
from sqlalchemy import text


async def test():
    query = "CS 4400 Spring 2025"
    embedding = await get_query_embedding(query)
    emb_list = str(embedding)  # "[0.1, 0.2, ...]" format
    
    # Use sync session for simpler SQL
    with SyncSessionLocal() as s:
        # Check distance to CS 4400 chunks specifically
        sql = f"""
            SELECT c.chunk_text, e.embedding <=> '{emb_list}'::vector AS d 
            FROM chunks c 
            JOIN embeddings e ON c.chunk_id = e.chunk_id 
            WHERE c.chunk_text ILIKE '%CS 4400%' AND c.chunk_text LIKE 'Course:%'
            ORDER BY d 
            LIMIT 5
        """
        r = s.execute(text(sql))
        print("CS 4400 chunk distances:")
        for row in r.fetchall():
            print(f"  {row[1]:.4f} | {row[0][:100]}...")


asyncio.run(test())
