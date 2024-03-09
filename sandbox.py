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


def setup_logger(name):
    """Sets up a logger for a given name."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Create a file handler that logs to a separate file for each named logger.
    file_handler = logging.FileHandler(f'logs/{name}.log', mode='w')
    file_handler.setLevel(logging.DEBUG)

    # Create a formatter and add it to the handler.
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    # Add the handler to the logger.
    logger.addHandler(file_handler)

    return logger


def multi_log(message, level=logging.INFO, loggers=None):
    """
    Logs a message to the specified named loggers.

    Args:
        message (str): The message to log.
        level (int): The logging level (e.g., logging.INFO, logging.ERROR).
        loggers (list[str]): A list of names of the loggers to log the message to.
    """

    # Log to the named loggers
    if loggers:
        for logger_name in loggers:
            logger = logging.getLogger(logger_name)
            logger.log(level, message)


# Setup individual loggers for specific functions
app_logger = setup_logger('app')
setup_logger('chat_completion')
setup_logger('text_chunker')
setup_logger('send_text')
setup_logger('listen')
setup_logger('stream')
setup_logger('get_remaining_chars_to_send')

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
            app_logger.error("mpv not found, necessary to stream audio. Install it for proper functionality.")
            raise ValueError("mpv not found, necessary to stream audio. Install instructions: https://mpv.io/installation/")

        if self.process is None or self.process.poll() is not None:
            multi_log("Starting mpv process", loggers=['app', 'stream'])
            self.process = subprocess.Popen(
                ["mpv", "--no-cache", "--no-terminal", "--", "fd://0"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

    def stop_process(self):
        if self.process and self.process.stdin:
            multi_log("Stopping mpv process...", loggers=['app', 'stream'])
            # self.process.terminate()
            self.process.stdin.close()
            self.process.wait()
            self.process = None
            multi_log("Stopped mpv process", loggers=['app', 'stream'])
            


async def text_chunker(input_queue, output_queue):
    """Split text into chunks, ensuring to not break sentences, and place them into an output queue."""
    splitters = (".", ",", "?", "!", ";", ":", "—", "-", "(", ")", "[", "]", "}", " ")
    buffer = ""

    logger = logging.getLogger('text_chunker')

    while True:
        text = await input_queue.get()
        if text is None:  # End of input
            multi_log("Text chunker reached end of text queue. text_chunker()", loggers=['app', 'text_chunker'])
            if buffer:
                await output_queue.put(buffer + " ")
            await output_queue.put(None)  # Signal completion
            break

        logger.debug(f"Chunker received text: '{text}'")
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
    
    logger = logging.getLogger('send_text')
    
    while True:
        chunked_text = await chunked_text_queue.get()
        if chunked_text is None:  # End of chunked text. Signal the end of the text stream
            multi_log("Send text reached end of chunked text queue. Sending EOS signal. send_text()", loggers=['app', 'send_text'])
            await websocket.send(json.dumps({"text": ""}))
            break
        text_message = {"text": chunked_text, "try_trigger_generation": True}
        logger.debug(f"Sending text to ElevenLabs for TTS: {text_message}")
        await websocket.send(json.dumps(text_message))
    


async def listen(websocket, audio_queue, chars_received):
    """Listen to the websocket for audio data and stream it."""

    logger = logging.getLogger('listen')
    multi_log("Started listening to websocket", loggers=['app', 'listen'])

    while True:
        message = await websocket.recv()
        data = json.loads(message)
        
        if data.get("audio"):   # Audio key might be absent, or value could be null. Don't proceed if either
            audio_data = base64.b64decode(data.pop('audio'))
            logger.debug(f"Data received (audio-omitted): {json.dumps(data)}")
            await audio_queue.put(audio_data)  # Place audio data into the queue
        else:
            logger.debug(f"Data received: {json.dumps(data)}")
        
        if data.get("normalizedAlignment"):   
            if data["normalizedAlignment"].get("chars"):
                chars_received.extend(data["normalizedAlignment"]["chars"])  # Accumulate received characters
        
        if data.get('isFinal'):
            multi_log("Received final audio response", loggers=['app', 'listen'])
            logger.debug(f"Chars received: {json.dumps(chars_received)}")
            await audio_queue.put(None)  # Signal the end of the stream
            break


async def stream(audio_queue):
    mpv_singleton = MPVProcessSingleton()
    mpv_singleton.start_process()
    mpv_process = mpv_singleton.process

    logger = logging.getLogger('stream')
    multi_log("Started streaming audio", loggers=['app', 'stream'])
    while True:
        chunk = await audio_queue.get()
        if chunk is None:  # Check for the signal to end streaming
            multi_log("Stream reached end of audio queue", loggers=['app', 'stream'])
            break
        mpv_process.stdin.write(chunk)
        mpv_process.stdin.flush()

    mpv_singleton.stop_process()


def get_remaining_chars_to_send(chars_to_send: list, chars_received: list) -> list:
    """Returns an array of the remaining characters that haven't been converted to speech."""

    logger = logging.getLogger('get_remaining_chars_to_send')

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
            # uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=eleven_monolingual_v1&xi_api_key={ELEVENLABS_API_KEY}"
            uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=eleven_multilingual_v2&xi_api_key={ELEVENLABS_API_KEY}"
            async with websockets.connect(uri) as websocket:
                app_logger.info("WebSocket connection established with ElevenLabs API.")
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
            app_logger.warning(f"WebSocket connection closed unexpectedly: {e}. Retrying...")
            
            remaining_chars = get_remaining_chars_to_send(chars_to_send, chars_received)

            # Add remaining text to queue
            text_queue = asyncio.Queue()
            await text_queue.put(''.join(remaining_chars))
            await text_queue.put(None)

            # Reset chars_to_send, chars_received, chunked_text queue, and audio_queue
            chars_to_send = remaining_chars
            chars_received = []
            chunked_text_queue = asyncio.Queue()


            
        except Exception as e:
            app_logger.error(f"An unexpected error occurred: {e}")
            break



async def chat_completion(query, text_queue, chars_to_send):
    logger = logging.getLogger('chat_completion')
    multi_log(f"Sending query to OpenAI: {query}", loggers=['app', 'chat_completion'])

    response = await aclient.chat.completions.create(
        model='gpt-4', 
        messages=[{'role': 'user', 'content': query}],
        temperature=1, 
        stream=True
    )

    response_content = []

    async for chunk in response:
        delta = chunk.choices[0].delta
        if delta.content is not None:
            if delta.content != "": # OpenAI usually starts response with empty string
                print(delta.content, end='', flush=True)
                logger.debug(f"Received content from OpenAI: {repr(delta.content)}")
                response_content.append(delta.content)
                await text_queue.put(delta.content)  # Place the content into the queue

                # Keep track of every char received
                for char in delta.content:
                    chars_to_send.append(char)
            
            else:
                logger.debug(f"Delta.content is empty string: {repr(delta.content)}")

        else:
            multi_log("Received end of OpenAI response", loggers=['app', 'chat_completion'])
            logger.info(f"Response content: {json.dumps(response_content)}")
            logger.debug(f"chars_to_send: {json.dumps(chars_to_send)}")
            await text_queue.put(None)  # Sentinel value to indicate no more items will be added
    

async def main():
    app_logger.info("Program started")
    # user_query = "Hello, tell me a short story in 100 words or less?"
    # user_query = "Hello, can you give me an inspirational quote from someone famous? I'm feeling a little tired but I want to get inspired to work hard today."
    user_query = "Hello, can you tell me a story that is exactly 500 words long?"
    # user_query = "Hello, can you summarize the tragedy of darth plageuis the wise in 100 words or less? Also, can you say it in a mix of english and spanish?"

    text_queue = asyncio.Queue()
    chars_to_send = []
    await asyncio.gather(
        chat_completion(user_query, text_queue, chars_to_send),
        text_to_speech_input_streaming(VOICE_ID, text_queue, chars_to_send)
    )
    app_logger.info("Program finished")


# Main execution
if __name__ == "__main__":
    asyncio.run(main())

    
