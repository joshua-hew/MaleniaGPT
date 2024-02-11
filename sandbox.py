import asyncio
import websockets
import json
import base64
import shutil
import os
import subprocess
import logging
from openai import AsyncOpenAI

# Setup file logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)  # Log everything at DEBUG level and above

# Setup logging to file for detailed debug information
logging.basicConfig(level=logging.DEBUG,  # Log everything at DEBUG level and above
                    filename='debug_log.log',  # Log messages are written to this file
                    filemode='w',  # Write to the log file
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Define API keys and voice ID
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
VOICE_ID = 'HxxnFvSdN4AyRUpj6yh7'

# Set OpenAI API key
aclient = AsyncOpenAI(api_key=OPENAI_API_KEY)

def is_installed(lib_name):
    return shutil.which(lib_name) is not None

async def text_chunker(chunks):
    """Split text into chunks, ensuring to not break sentences."""
    splitters = (".", ",", "?", "!", ";", ":", "—", "-", "(", ")", "[", "]", "}", " ")
    buffer = ""

    async for text in chunks:
        if text is None:
            logging.warning("Received None in text_chunker")
            continue  # Skip this iteration if text is None
        logging.debug(f"Chunker received text: {text}")
        if buffer.endswith(splitters):
            yield buffer + " "
            buffer = text
        elif text.startswith(splitters):
            yield buffer + text[0] + " "
            buffer = text[1:]
        else:
            buffer += text

    if buffer:
        yield buffer + " "

async def stream(audio_stream):
    """Stream audio data using mpv player."""
    if not is_installed("mpv"):
        logging.error("mpv not found, necessary to stream audio. Install it for proper functionality.")
        raise ValueError(
            "mpv not found, necessary to stream audio. "
            "Install instructions: https://mpv.io/installation/"
        )

    mpv_process = subprocess.Popen(
        ["mpv", "--no-cache", "--no-terminal", "--", "fd://0"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    logging.info("Started streaming audio")
    async for chunk in audio_stream:
        if chunk:
            logging.debug("Streaming audio chunk of size: {}".format(len(chunk)))
            mpv_process.stdin.write(chunk)
            mpv_process.stdin.flush()

    if mpv_process.stdin:
        mpv_process.stdin.close()
    mpv_process.wait()

async def text_to_speech_input_streaming(voice_id, text_iterator):
    """Send text to ElevenLabs API and stream the returned audio."""
    uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=eleven_monolingual_v1"

    async with websockets.connect(uri) as websocket:
        logging.info("WebSocket connection established with ElevenLabs API.")
        init_message = {
            "text": " ",
            "voice_settings": {"stability": 0.70, "similarity_boost": 0.75},
            "xi_api_key": ELEVENLABS_API_KEY,
        }
        logging.debug(f"Sending initial message to ElevenLabs: {init_message}")
        await websocket.send(json.dumps(init_message))

        async def listen():
            """Listen to the websocket for audio data and stream it."""
            while True:
                try:
                    message = await websocket.recv()
                    logging.debug(f"Received message from ElevenLabs: {message}")
                    data = json.loads(message)
                    if data.get("audio"):
                        audio_data = base64.b64decode(data["audio"])
                        logging.debug(f"Decoded audio chunk size: {len(audio_data)}")
                        yield audio_data
                    elif data.get('isFinal'):
                        break
                except websockets.exceptions.ConnectionClosed as e:
                    logging.error(f"WebSocket connection closed unexpectedly: {e}.")
                    break
                except Exception as e:
                    logging.error(f"An unexpected error occurred: {e}.")
                    break  # Exit the loop in case of unexpected errors

        listen_task = asyncio.create_task(stream(listen()))

        async for text in text_chunker(text_iterator):
            text_message = {"text": text, "try_trigger_generation": True}
            logging.debug(f"Sending text to ElevenLabs for TTS: {text_message}")
            await websocket.send(json.dumps(text_message))

        await websocket.send(json.dumps({"text": ""}))

        await listen_task

async def chat_completion(query):
    """Retrieve text from OpenAI and pass it to the text-to-speech function."""
    logging.info(f"Sending query to OpenAI: {query}")
    response = await aclient.chat.completions.create(model='gpt-4', messages=[{'role': 'user', 'content': query}],
                                                     temperature=1, stream=True)

    async def text_iterator():
        async for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content is not None:  # Skip None values
                print(delta.content, end='', flush=True)  # Print responses incrementally without newline
                logging.debug(f"Received content from OpenAI: {delta.content}")
                yield delta.content
            else:
                logging.warning("Received None from OpenAI response")

    await text_to_speech_input_streaming(VOICE_ID, text_iterator())

# Main execution
if __name__ == "__main__":

    logging.debug("Program started")
    # user_query = "Hello, tell me a very short story."
    user_query = "Hi Malenia, can you tell me a story with a minimum of 800 words?"
    try:
        asyncio.run(chat_completion(user_query))
    except Exception as e:
        logging.error(f"An error occurred: {e}")
    logging.debug("Program finished")
