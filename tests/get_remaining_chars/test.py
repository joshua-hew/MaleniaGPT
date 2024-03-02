import json
import unicodedata



def get_remaining_chars_to_send(chars_to_send: list, chars_received: list):
    """Returns an array of the remaining characters that haven't been converted to speech."""

    def remove_accents(input_str):
        nfkd_form = unicodedata.normalize('NFKD', input_str)
        return ''.join([c for c in nfkd_form if not unicodedata.combining(c)])

    def replace_newlines(arr):
        result = []
        for char in arr:
            if char == "\n":
                if not result or result[-1] != " ":
                    result.append(" ")
            else:
                result.append(char)
        return result

    # Normalize the chars_to_send. Change special characters to unicode base chars. ex: "é" -> "e".
    chars_to_send_normalized = [remove_accents(c) for c in chars_to_send]
    # print(json.dumps(chars_to_send_normalized))

    # Replace newlines with a " ". Mimic the internal behavior of ElevenLabs
    chars_to_send_normalized = replace_newlines(chars_to_send_normalized)
    print(json.dumps(chars_to_send_normalized))
    
    
    # Remove leading space in chars_received to make the comparison easier
    chars_received_formatted = chars_received[1:]
    # print(json.dumps(chars_received_formatted[:100]))




    # # Determine where to continue in the text queue.
    # # Continue point is the index after the last character received succesfully. 
    # continue_point = None
    # for i in range(len(chars_to_send_normalized)):
    #     char = chars_to_send_normalized[i]

    #     if i < len(chars_received_formatted):
    #         received_char = chars_received_formatted[i]
    #         if char != received_char:
    #             raise Exception("Error: chars do not match. Expected char to send to match char received.")
        
    #     else: # If reached end of received chars
    #         continue_point = i

    # remaining_chars = chars_to_send[continue_point:]



    # return remaining_chars

    



if __name__ == '__main__':
    with open('chars_to_send.json', 'r') as f:
        chars_to_send = json.load(f)

    with open('chars_received.json', 'r') as f:
        chars_received = json.load(f)
    
    remaining_chars = get_remaining_chars_to_send(chars_to_send, chars_received)