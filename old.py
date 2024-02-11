import os
import json
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "<your OpenAI API key if not set as env var>"))

def start_chat():
    # Start a new chat session (this will keep the context)
    chat_log = []
    system_persona_primer_msg = ""

    with open("system-msg.txt", "r") as f:
        system_persona_primer_msg = f.read()

    while True:
        # Get user input
        user_message = input("You: ")
        
        # Check if user wants to end the conversation
        if user_message.lower() == 'exit':
            print("Exiting chat.")
            break
        
        # Create a completion request
        response = client.chat.completions.create(
            model="gpt-4",  # or the model of your choice
            messages=[
                {"role": "system", "content": system_persona_primer_msg},
                {"role": "user", "content": user_message},
            ] + chat_log
        )
        
        # Extract the message content from the response
        message_content = response.choices[0].message.content

        # DEBUG
        print(json.dumps(json.loads(response.model_dump_json()), indent=4))
        
        # Print the AI response
        print(f"Malenia: {message_content}")
        
        # Save the conversation log to maintain context
        chat_log.append({"role": "user", "content": user_message})
        chat_log.append({"role": "assistant", "content": message_content})


        # Send the response to ElevenLabs to be dubbed
        

if __name__ == "__main__":
  start_chat()
