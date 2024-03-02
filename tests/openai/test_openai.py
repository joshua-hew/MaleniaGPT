import os
import json
import base64
import asyncio
import unittest
import websockets
from openai import AsyncOpenAI


# Environment variables
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
VOICE_ID = 'HxxnFvSdN4AyRUpj6yh7'

# Set OpenAI API key
aclient = AsyncOpenAI(api_key=OPENAI_API_KEY)


async def chat_completion(query, text_array):
    # logging.info(f"Sending query to OpenAI: {query}")
    response = await aclient.chat.completions.create(model='gpt-4', messages=[{'role': 'user', 'content': query}],
                                                     temperature=1, stream=True)

    async for chunk in response:
        delta = chunk.choices[0].delta
        if delta.content is not None:
            # print(delta.content, end='', flush=True)
            # logging.debug(f"Received content from OpenAI: {delta.content}")
            # await text_queue.put(delta.content)  # Place the content into the queue

            text_array.append(delta.content)

            # # Keep track of every char received
            # for char in delta.content:
            #     chars_to_send.append(char)

        else:
            # logging.info("Received end of OpenAI response")
            # await text_queue.put(None)  # Sentinel value to indicate no more items will be added
            pass


def test_openai_response():
    # Test 1
    text_array = []
    user_query = "Hi ChatGPT, please only respond with the following text. Do not include anything else in your response: Hello\nWorld"
    asyncio.run(chat_completion(user_query, text_array))
    print(json.dumps(text_array))
    
    # # Test 2
    # text_array = []
    # user_query = "Hi ChatGPT, please only respond with the following text. Do not include anything else in your response: Hello\n\nWorld"
    # asyncio.run(chat_completion(user_query, text_array))
    # print(json.dumps(text_array))


if __name__ == "__main__":
    test_openai_response()