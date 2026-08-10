import asyncio
from google import genai
import os

async def main():
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", "dummy"))
    print(type(client.aio.models.generate_content_stream))
    
asyncio.run(main())
