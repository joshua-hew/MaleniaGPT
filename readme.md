brew install mpv

# Todo
- [x] Multilingual
- [x] User input loop
- [ ] Remember previous messages in conversation. Implement token limit for message history.
- [ ] Voice control / hands free mode
- [ ] do stuff - open up apps on computer interact with them


# Bugs
1. For longer stories, the voice response ends prematurely
  - Fix bug where "..." is being broken up by the chunker
  - Refactor code using classes, to make shared variable reference easier.


pip freeze >  requirements.txt

# System Setup (Mac)
brew install mpv
brew install portaudio

export CFLAGS="-I$(brew --prefix)/include"
export LDFLAGS="-L$(brew --prefix)/lib"


# Dev Setup
python version: 3.12.2

python3 -m venv venv

On mac:
source venv/bin/activate

pip install -r requirements.txt