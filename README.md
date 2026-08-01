DS Streamer: A screen-streaming tool that casts your PC display directly to a Nintendo DS over local Wi-Fi in real-time.

What You Need:
- A Nintendo DS flashcart or a DS emulator that supports Wi-Fi simulation (such as **MelonDS** or **No$GBA**).
- A powerful enough computer to run the Python code
- Python 3.8 or higher (even 3.14 works)
- Run "pip install Pillow mss pynput"

How to Run:
1. Open the DSstream.py file
2. Change your settings (3 presets are present)
3. Click on the "Start Stream" button. The server will listen on port `8888`.
4. Boot "ds-stream-client.nds" on your DS hardware or emulator.
5. Press X to open the Server IP Editor, and use the D-pad to enter your host PC's local IP address. Once entered, press A.
6. Press B to connect to your PC and start streaming.
7. Enjoy!

Disclaimers:
- Multiple consoles at a time weren't tested and aren't recommended.
- The framerate cannot go higher than the framerate in the live stats section.
- Beware that the DS isn't powerful and that its Wifi is slow.
- Memory leaks are a possibility.
- This project was made using AIs, as I am not a good enough coder to do that on my own. Sorry to people who thought it was made by hand.
