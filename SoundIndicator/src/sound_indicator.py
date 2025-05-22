from machine import Pin, UART#, lightsleep
import time

# Pin assignments
POWER_PIN = 7                  # GPIO controlling DFPlayer power (High = ON, Low = OFF)
TRACK_BUTTON_PINS = [2, 3, 4, 5]   # GP2-GP5: track select buttons 1-4
STOP_BUTTON_PIN = 6            # GP6: stop button

# Initialize DFPlayer power control pin
power_pin = Pin(POWER_PIN, Pin.OUT)
power_pin.value(0)  # Ensure DFPlayer is off initially

# Initialize UART for DFPlayer (UART0 on GP0 (TX) and GP1 (RX) at 9600 baud)
uart = UART(0, baudrate=9600, tx=Pin(0), rx=Pin(1))

# DFPlayer command bytes (per DFPlayer protocol)
CMD_PLAY_TRACK = 0x03  # command to play specified track number:contentReference[oaicite:6]{index=6}
CMD_STOP       = 0x16  # command to stop playback:contentReference[oaicite:7]{index=7}

# Global state flags
track_to_play = None    # Track number (1-4) requested to play
stop_requested = False  # Flag set when stop button is pressed during playback
playing = False         # True if a track is currently playing

# Factory function to create an IRQ handler for each button
def make_button_handler(button_index):
    """
    Returns an interrupt handler that captures the given button index.
    button_index: 1-4 for track buttons, 5 for stop button.
    """
    last_press_time = 0  # for basic debounce
    
    def handler(pin):
        nonlocal last_press_time
        t = time.ticks_ms()
        if time.ticks_diff(t, last_press_time) < 200:  # 200ms debounce interval
            return  # Ignore rapid bouncing presses
        last_press_time = t
        
        global track_to_play, stop_requested, playing
        if button_index == 5:  # Stop button pressed
            if playing:
                stop_requested = True  # signal to stop playback
        else:
            # Track button pressed (1-4)
            if not playing:
                # Only set new track if not already playing something
                track_to_play = button_index  # remember which track to play
        # Waking from lightsleep is automatic on interrupt, nothing else needed in IRQ
    return handler

# Set up button input pins with pull-ups and attach interrupts
# Track buttons:
for i, gp in enumerate(TRACK_BUTTON_PINS, start=1):  # enumerate 1-4
    pin = Pin(gp, Pin.IN, Pin.PULL_UP)
    pin.irq(trigger=Pin.IRQ_FALLING, handler=make_button_handler(i))
# Stop button:
stop_pin = Pin(STOP_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
stop_pin.irq(trigger=Pin.IRQ_FALLING, handler=make_button_handler(5))

# Helper function to send a command to DFPlayer over UART
def send_dfplayer_command(cmd, param1, param2):
    """
    Send a 10-byte command packet to DFPlayer.
    cmd: Command byte (e.g., 0x03 for play, 0x16 for stop).
    param1, param2: High and low bytes of the 16-bit command parameter.
    """
    # Construct packet as bytearray for speed
    packet = bytearray(10)
    packet[0] = 0x7E   # start byte
    packet[1] = 0xFF   # version
    packet[2] = 0x06   # length
    packet[3] = cmd    # command
    packet[4] = 0x00   # feedback (0x00 = no confirmation feedback)
    packet[5] = param1 # high byte of parameter
    packet[6] = param2 # low byte of parameter
    # Calculate checksum (16-bit) = 0xFFFF - (sum of bytes 1..6) + 1
    checksum = 0xFFFF - ((packet[1] + packet[2] + packet[3] + 
                           packet[4] + packet[5] + packet[6]) & 0xFFFF) + 1
    packet[7] = (checksum >> 8) & 0xFF   # checksum high byte
    packet[8] = checksum & 0xFF         # checksum low byte
    packet[9] = 0xEF   # end byte
    uart.write(packet)

# Main loop: wait for button presses and handle actions
time.sleep_ms(3000)
while True:
    # Enter low-power mode until an interrupt occurs (button press wakes the MCU)
    if not playing:
        #lightsleep()  # Sleep indefinitely until a hardware interrupt wakes us
        time.sleep_ms(10)
    
    # Upon waking, check what caused the wake (which flag is set):
    if stop_requested:
        # Stop button was pressed (this flag is only set if we were playing)
        if playing:
            # Send stop command to DFPlayer to halt playback immediately
            send_dfplayer_command(CMD_STOP, 0x00, 0x00)
            time.sleep_ms(50)  # short delay to allow the stop command to take effect
        # Ensure DFPlayer power is off (if it was on, turn it off)
        power_pin.value(0)
        # Reset state
        playing = False
        stop_requested = False
        track_to_play = None
        # Loop back to wait for the next button press
        continue
    
    if track_to_play is not None:
        # A track button was pressed and no track was playing, so we have a new track to play.
        track_num = track_to_play
        track_to_play = None  # consume this request
        
        # Power on the DFPlayer module
        power_pin.value(1)
        time.sleep_ms(200)  # give DFPlayer time to boot up and read the SD card
        
        # Flush any startup data from DFPlayer (if any)
        while uart.any():
            uart.read()  # discard any noise or startup messages
        
        # Send play command for the selected track number
        # (track_num is 1-4 in this application)
        send_dfplayer_command(CMD_PLAY_TRACK, (track_num >> 8) & 0xFF, track_num & 0xFF)
        playing = True  # mark that a track is now playing
        
        # Monitor UART for track completion or a stop request
        track_finished = False
        while True:
            if stop_requested:
                # Stop button pressed during playback
                break  # exit loop to stop playback
            # Check if DFPlayer sent any data (e.g., track finished code)
            if uart.any():
                data = uart.read()  # read all available bytes from UART buffer
                if data is not None:
                    # Look for 0x3D in the response (track finished code from DFPlayer):contentReference[oaicite:8]{index=8}
                    if data.find(b'\x3D') != -1:
                        track_finished = True
                        break  # track has finished playing
            # If no stop or finish yet, small delay before checking again (to reduce CPU usage)
            time.sleep_ms(10)
        
        # Playback ended either by stop request or natural completion
        if stop_requested:
            # If stop was requested, ensure playback is stopped
            send_dfplayer_command(CMD_STOP, 0x00, 0x00)  # stop the track immediately
            time.sleep_ms(50)  # allow time for the stop command to process
        
        # Turn off DFPlayer power to save energy
        power_pin.value(0)
        playing = False
        stop_requested = False
        # Loop will go back to sleep on next iteration
