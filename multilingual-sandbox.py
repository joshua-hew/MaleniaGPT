import os
import json
import base64
import shutil
import logging
import asyncio
import subprocess
import websockets
from openai import AsyncOpenAI
from unidecode import unidecode


# Setup file logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)  # Log everything at DEBUG level and above

# Setup logging to file for detailed debug information
file_handler_debug = logging.FileHandler('logs/debug_log.log', mode='w')
file_handler_debug.setLevel(logging.DEBUG)
file_handler_debug.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Setup logging to file for info messages
file_handler_info = logging.FileHandler('logs/info_log.log', mode='w')
file_handler_info.setLevel(logging.INFO)
file_handler_info.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Add handlers to the logger
logger.addHandler(file_handler_debug)
logger.addHandler(file_handler_info)


# Define API keys and voice ID
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
VOICE_ID = 'HxxnFvSdN4AyRUpj6yh7'

# Set OpenAI API key
aclient = AsyncOpenAI(api_key=OPENAI_API_KEY)

class MPVProcessSingleton:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MPVProcessSingleton, cls).__new__(cls)
            cls._instance.process = None
        return cls._instance

    def is_installed(self, lib_name):
        return shutil.which(lib_name) is not None

    def start_process(self):
        if not self.is_installed("mpv"):
            logging.error("mpv not found, necessary to stream audio. Install it for proper functionality.")
            raise ValueError("mpv not found, necessary to stream audio. Install instructions: https://mpv.io/installation/")

        if self.process is None or self.process.poll() is not None:
            self.process = subprocess.Popen(
                ["mpv", "--no-cache", "--no-terminal", "--", "fd://0"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

    def stop_process(self):
        if self.process and self.process.stdin:
            self.process.stdin.close()
            self.process.wait()
            self.process = None


async def text_chunker(input_queue, output_queue):
    """Split text into chunks, ensuring to not break sentences, and place them into an output queue."""
    splitters = (".", ",", "?", "!", ";", ":", "—", "-", "(", ")", "[", "]", "}", " ")
    buffer = ""

    while True:
        text = await input_queue.get()
        if text is None:  # End of input
            logger.info("Text chunker reached end of text queue. text_chunker()")
            if buffer:
                await output_queue.put(buffer + " ")
            await output_queue.put(None)  # Signal completion
            break

        logging.debug(f"Chunker received text: {text}")
        if buffer.endswith(splitters):
            await output_queue.put(buffer + " ")
            buffer = text
        elif text.startswith(splitters):
            await output_queue.put(buffer + text[0] + " ")
            buffer = text[1:]
        else:
            buffer += text


async def send_text(websocket, chunked_text_queue):
    """Send chunked text from the queue to ElevenLabs API."""
    while True:
        chunked_text = await chunked_text_queue.get()
        if chunked_text is None:  # End of chunked text
            # Signal the end of the text stream
            logger.info("Send text reached end of chunked text queue. Sending EOS signal. send_text()")
            await websocket.send(json.dumps({"text": ""}))
            break
        text_message = {"text": chunked_text, "try_trigger_generation": True}
        logging.debug(f"Sending text to ElevenLabs for TTS: {text_message}")
        await websocket.send(json.dumps(text_message))
    


async def listen(websocket, audio_queue, chars_received):
    """Listen to the websocket for audio data and stream it."""

    while True:
        message = await websocket.recv()
        data = json.loads(message)
        if data.get("audio"):
            audio_data = base64.b64decode(data["audio"])
            # logging.debug(f"Decoded audio chunk size: {len(audio_data)}")
            await audio_queue.put(audio_data)  # Place audio data into the queue
        if "normalizedAlignment" in data and data["normalizedAlignment"] is not None and "chars" in data["normalizedAlignment"]:
            chars_received.extend(data["normalizedAlignment"]["chars"])  # Accumulate received characters
        if data.get('isFinal'):
            logger.info("Received final audio response. listen()")
            await audio_queue.put(None)  # Signal the end of the stream
            break


async def stream(audio_queue):
    mpv_singleton = MPVProcessSingleton()
    mpv_singleton.start_process()
    mpv_process = mpv_singleton.process

    logging.info("Started streaming audio")
    while True:
        chunk = await audio_queue.get()
        if chunk is None:  # Check for the signal to end streaming
            logging.info("Stream reached end of audio queue. Stopped streaming audio.")
            break
        mpv_process.stdin.write(chunk)
        mpv_process.stdin.flush()

    mpv_singleton.stop_process()


def get_remaining_chars_to_send(chars_to_send: list, chars_received: list) -> list:
    """Returns an array of the remaining characters that haven't been converted to speech."""

    # Log inputs for troubleshooting
    logger.debug(f"Characters to send. Len: {len(chars_to_send)}.")
    logger.debug(f"{json.dumps(chars_to_send)}")
    logger.debug(f"Characters received. Len: {len(chars_received)}.")
    logger.debug(f"{json.dumps(chars_received)}")


    # Format chars to send for easier comparison with characters received
    chars_to_send_formatted = [unidecode(c) for c in chars_to_send]

    # Format chars received for easier comparison
    chars_received_formatted = chars_received[1:]   # Remove leading space in chars_received
    
    # Log formatted versions for troubleshooting
    logger.debug(f"Characters to send formatted. Len: {len(chars_to_send_formatted)}.")
    logger.debug(f"{json.dumps(chars_to_send_formatted)}")
    logger.debug(f"Characters received formatted. Len: {len(chars_received_formatted)}.")
    logger.debug(f"{json.dumps(chars_received_formatted)}")


    # Determine where to continue in the text queue.
    # Continue point is the index after the last character received succesfully. 
    continue_point = None
    
    # The index in the second array.
    # This is where the matching character should be in the second array.
    # The matching character is allowed to be at most (j + displacement tolerance away)
    j = 0
    displacement_tolerance = 2

    for i in range(len(chars_to_send_formatted)):
        # The character to match
        char = chars_to_send_formatted[i]

        if char == "\n":
            continue

        # If j pointer in bounds:
        if j < len(chars_received_formatted):
            
            # The matching character is allowed to be at most (j + displacement tolerance away)
            # If the matching character is not exactly at j, but within the tolerance, update j, and log that occurrence
            # Else, throw an error and populate logs
            try:
                index_of_next_matching_char = chars_received_formatted.index(char, j)
                if index_of_next_matching_char == j:
                    pass
                
                elif index_of_next_matching_char - j <= displacement_tolerance:
                    logger.warning(f"Idiosyncracy found. Expected matching char '{char}' to be at {j}. Was found at {index_of_next_matching_char}")
                    logger.debug(f"Context for chars_to_send: {json.dumps(chars_to_send_formatted[max(0, i-10):i+10])}")
                    logger.debug(f"Context for chars_received: {json.dumps(chars_received_formatted[max(0, j-10):j+10])}")

                    # Set j to index of matching char 
                    j = index_of_next_matching_char
                
                else:
                    logger.error(f"Index of next matching char not within tolerance. Expected char '{char}' to be at {j}. Was found at {index_of_next_matching_char}. Displacement tolerance: {displacement_tolerance}")
                    logger.error(f"Context for chars_to_send: {json.dumps(chars_to_send_formatted[max(0, i-10):i+10])}")
                    logger.error(f"Context for chars_received (centered at j): {json.dumps(chars_received_formatted[max(0, j-10):j+10])}")
                    logger.error(f"Context for chars_received (centered at index of next_next_matching_char): {json.dumps(chars_received_formatted[max(0, index_of_next_matching_char-10):index_of_next_matching_char+10])}")
                    raise Exception(f"Could not confirm that the current char was received within the displacement tolerance. Char: '{char}'")

                # Increment j
                j += 1
            

            except ValueError as e: # If can't find matching char in chars received
                logger.warning(f"Index() could not find a match for char '{char}' at position {i}")
                logger.debug(f"Context for chars_to_send: {json.dumps(chars_to_send_formatted[max(0, i-10):i+10])}")
                logger.debug(f"Context for chars_received (centered at j): {json.dumps(chars_received_formatted[max(0, j-10):j+10])}")
                continue_point = i
                break


        else: # If reached end of received chars, then the current un-matched char is the continue point
            logger.info("Found continue point")
            continue_point = i
            break


    remaining_chars = chars_to_send[continue_point:]

    logger.debug(f"Remaining chars. Len {len(remaining_chars)}")
    logger.debug(f"{json.dumps(remaining_chars)}")
    return remaining_chars


async def text_to_speech_input_streaming(voice_id, text_queue, chars_to_send):
    chunked_text_queue = asyncio.Queue() 
    audio_queue = asyncio.Queue()
    chars_received = []

    while True:
        try:
            uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=eleven_multilingual_v2&xi_api_key={ELEVENLABS_API_KEY}"
            async with websockets.connect(uri) as websocket:
                logging.info("WebSocket connection established with ElevenLabs API.")
                init_message = {
                    "text": " ",
                    "voice_settings": {"stability": 0.70, "similarity_boost": 0.75},
                    "xi_api_key": ELEVENLABS_API_KEY,
                }
                await websocket.send(json.dumps(init_message))

                # Start the text_chunker, send_text, listen, and stream concurrently
                await asyncio.gather(
                    text_chunker(text_queue, chunked_text_queue),
                    send_text(websocket, chunked_text_queue),
                    listen(websocket, audio_queue, chars_received),
                    stream(audio_queue)
                )
                
            break  # Exit the loop if everything went well
        
        except websockets.exceptions.ConnectionClosed as e:
            logging.warning(f"WebSocket connection closed unexpectedly: {e}. Retrying...")
            
            remaining_chars = get_remaining_chars_to_send(chars_to_send, chars_received)

            # Add remaining text to queue
            text_queue = asyncio.Queue()
            await text_queue.put(''.join(remaining_chars))
            await text_queue.put(None)

            # Reset chars_to_send, chars_received, chunked_text queue, and audio_queue
            chars_to_send = remaining_chars
            chars_received = []
            chunked_text_queue = asyncio.Queue()
            audio_queue = asyncio.Queue()


            
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            break



async def chat_completion(query, text_queue, chars_to_send):
    logging.info(f"Sending query to OpenAI: {query}")
    response = await aclient.chat.completions.create(model='gpt-4', messages=[{'role': 'user', 'content': query}],
                                                     temperature=1, stream=True)

    async for chunk in response:
        delta = chunk.choices[0].delta
        if delta.content is not None:
            print(delta.content, end='', flush=True)
            logging.debug(f"Received content from OpenAI: {delta.content}")
            await text_queue.put(delta.content)  # Place the content into the queue

            # Keep track of every char received
            for char in delta.content:
                chars_to_send.append(char)

        else:
            logging.info("Received end of OpenAI response")
            await text_queue.put(None)  # Sentinel value to indicate no more items will be added
    

async def main():
    logging.info("Program started")
    # user_query = "Hello, tell me a very short story."
    # user_query = "Hey there, can you give me an inspirational quote from someone famous? I'm feeling a little tired but I want to get inspired to work hard today."
    # user_query = "Hello, can you tell me a story that is exactly 500 words long?"
    # user_query = "Hello, can you tell me a shorty story in spanish and under 100 words long?"
    user_query = "Hello, can you tell me a shorty story in japanese and under 100 words long?"

    text_queue = asyncio.Queue()
    chars_to_send = []
    await asyncio.gather(
        chat_completion(user_query, text_queue, chars_to_send),
        text_to_speech_input_streaming(VOICE_ID, text_queue, chars_to_send)
    )
    logging.info("Program finished")


# Main execution
if __name__ == "__main__":
    asyncio.run(main())

    
