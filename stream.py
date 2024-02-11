import json
import base64
import shutil
import subprocess
import os

def is_installed(lib_name):
    """Check if a library/executable is installed."""
    return shutil.which(lib_name) is not None

def play_audio_from_file(filename):
    """Read base64 encoded audio from a JSON file and play it."""
    if not is_installed("mpv"):
        print("mpv is not installed. Please install mpv to play audio.")
        return
    
    try:
        # Load the JSON file
        with open(filename, 'r') as file:
            data = json.load(file)
        
        # Assuming the JSON structure is { "audio": "base64_encoded_string" }
        audio_base64 = data['audio']
        audio_bytes = base64.b64decode(audio_base64)
        
        # Play the audio using mpv
        process = subprocess.Popen(['mpv', '--no-terminal', '--', '-'],
                                   stdin=subprocess.PIPE)
        process.stdin.write(audio_bytes)
        process.stdin.flush()
        process.stdin.close()
        process.wait()
        print("Audio playback finished.")
        
    except Exception as e:
        print(f"Failed to play audio: {e}")

if __name__ == "__main__":
    play_audio_from_file('sample_audio.json')
