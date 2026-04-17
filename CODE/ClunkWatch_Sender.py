from machine import ADC, Pin, I2C, PWM
import neopixel
import time
import network
import socket

# ====================== HARDWARE SETUP ======================
PEER_IP = "192.168.4.1"
MSG_PORT = 5000

pulse = ADC(26)
leds = neopixel.NeoPixel(Pin(15), 8)
i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=400000)
speaker = PWM(Pin(2))
speaker.duty_u16(0)

joy_x = ADC(Pin(27))
joy_y = ADC(Pin(28))
joy_btn = Pin(3, Pin.IN, Pin.PULL_UP)
game_btn = Pin(4, Pin.IN, Pin.PULL_UP)
back_btn = Pin(6, Pin.IN, Pin.PULL_UP)

# ====================== LCD SETTINGS ======================
USE_LCD = True
LCD_ADDR = 0x27
LCD_BACKLIGHT = 0x08
LCD_ENABLE = 0x04
LCD_RS = 0x01

def lcd_write_byte(data):
    if not USE_LCD: return
    try:
        i2c.writeto(LCD_ADDR, bytes([data | LCD_BACKLIGHT]))
    except:
        pass

def lcd_pulse_enable(data):
    lcd_write_byte(data | LCD_ENABLE)
    time.sleep_us(1)
    lcd_write_byte(data & ~LCD_ENABLE)
    time.sleep_us(50)

def lcd_send_nibble(nibble, mode):
    lcd_write_byte(nibble | mode)
    lcd_pulse_enable(nibble | mode)

def lcd_send_byte(byte, mode):
    lcd_send_nibble(byte & 0xF0, mode)
    lcd_send_nibble((byte << 4) & 0xF0, mode)

def lcd_command(cmd):
    lcd_send_byte(cmd, 0x00)

def lcd_char(char):
    lcd_send_byte(ord(char), LCD_RS)

def lcd_init():
    if not USE_LCD: return
    time.sleep_ms(100)
    for _ in range(3):
        lcd_send_nibble(0x30, 0)
        time.sleep_ms(10)
    lcd_send_nibble(0x20, 0)
    time.sleep_ms(5)
    lcd_command(0x28)
    lcd_command(0x08)
    lcd_command(0x01)
    time.sleep_ms(5)
    lcd_command(0x06)
    lcd_command(0x0C)

def lcd_set_cursor(row, col):
    offsets = [0x00, 0x40]
    lcd_command(0x80 | (offsets[row] + col))

def lcd_clear_print(row, text):
    if not USE_LCD: return
    text = text[:16]
    while len(text) < 16:
        text += " "
    lcd_set_cursor(row, 0)
    for char in text:
        lcd_char(char)

def lcd_create_char(location, charmap):
    lcd_command(0x40 | (location << 3))
    for line in charmap:
        lcd_send_byte(line, LCD_RS)
    lcd_command(0x80)

def beep(freq=1000, duration_ms=100):
    speaker.freq(freq)
    speaker.duty_u16(32768)
    time.sleep_ms(duration_ms)
    speaker.duty_u16(0)

# ====================== SOCKET SERVER (for receiving) ======================
server_sock = None
msg_received = ""

def start_sender_server():
    global server_sock
    try:
        server_sock = socket.socket()
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(('', MSG_PORT))
        server_sock.listen(1)
        server_sock.setblocking(False)
        print("Sender server listening on port", MSG_PORT)
    except Exception as e:
        print("Sender server error:", e)
        server_sock = None

def check_incoming():
    global msg_received
    if server_sock is None:
        return
    try:
        conn, addr = server_sock.accept()
        conn.settimeout(2)
        data = conn.recv(64)
        if data:
            msg_received = data.decode().strip()
            print("Received:", msg_received)
            for i in range(8):
                leds[i] = (0, 60, 60)
            leds.write()
            beep(1600, 150)
            time.sleep_ms(300)
            for i in range(8):
                leds[i] = (0, 0, 0)
            leds.write()
        conn.close()
    except OSError as e:
        if e.args[0] != 11:
            print("Socket error:", e)
    except Exception as e:
        print("Unexpected error:", e)

# ====================== STA MODE (Sender) ======================
wifi_connected = False

def start_sta_mode():
    global wifi_connected
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.connect("PicoChat", "picopico123")
    lcd_clear_print(0, "Connecting...")
    lcd_clear_print(1, "PicoChat")
    timeout = 0
    while not sta.isconnected() and timeout < 20:
        time.sleep_ms(500)
        timeout += 1
    if sta.isconnected():
        ip = sta.ifconfig()[0]
        print("Connected! IP:", ip)
        wifi_connected = True
        start_sender_server()
        lcd_clear_print(0, "Connected!")
        lcd_clear_print(1, ip)
    else:
        lcd_clear_print(0, "Failed!")
        lcd_clear_print(1, "Boot receiver 1st")
        print("WiFi connection failed")
    time.sleep_ms(1500)

# ====================== PULSE SENSOR ======================
NUM_LEDS = 8
MIN_INTERVAL = 500
MAX_INTERVAL = 1500
BPM_ALPHA = 0.15
WAVE_SPEED = 0.035
HIGH_BPM = 110
last_beat = 0
last_finger_seen = 0
bpm = 0.0
wave_progress = 0.0
in_beat = False
rolling_avg = 0
AVG_ALPHA = 0.02
AVG_ALPHA_FAST = 0.15
FINGER_TIMEOUT = 2000

# ====================== MENU ======================
SCREEN_WIFI = 0
SCREEN_HELP = 1
SCREEN_BATTERY = 2
SCREEN_HEART = 3
SCREEN_TIME = 4
SCREEN_GAME = 5
SCREEN_INBOX = 6
SCREEN_COMPOSE = 7
NUM_SCREENS = 8

current_screen = SCREEN_HEART
prev_screen = -1
last_joy_move = 0
JOY_DEBOUNCE = 400
JOY_CENTER_X = 51000
JOY_CENTER_Y = 50900
JOY_DEADZONE = 10000
last_screen_content = ("", "")
last_lcd_update = 0
LCD_UPDATE_MS = 500
joy_btn_held_start = 0
last_beep = 0
BEEP_INTERVAL = 600

help_lines = [
    "== HOW TO USE ==", "Joystick L/R:", "Switch screens", "",
    "Joystick U/D:", "Scroll / change", "",
    "Joystick press:", "Send / select", "(hold = exit)", "",
    "Top button:", "Game / confirm", "",
    "Back button:", "Delete / go back", "",
    "Heart screen:", "Place finger", "",
    "Msg screen:", "Type & send msgs", "",
    "WiFi screen:", "Join PicoChat AP",
]
help_index = 0

# ====================== GAME ======================
GAME_IDLE = 0
GAME_PLAYING = 1
GAME_OVER = 2
CHAR_STEVE_A = 0
CHAR_STEVE_B = 1
CHAR_STEVE_J = 2
CHAR_CACTUS = 3
CHAR_WALL = 4
CHAR_BIRD = 5

game_state = GAME_IDLE
game_score = 0
obstacle_x = 15
obstacle_type = 0
last_obstacle_move = 0
is_jumping = False
jump_start = 0
last_btn_press = 0
BTN_DEBOUNCE = 200
PLAYER_COL = 1
JUMP_DURATION = 500
steve_frame = 0
last_steve_frame = 0
STEVE_FRAME_MS = 200

def game_load_chars():
    lcd_create_char(CHAR_STEVE_A, [0b01110,0b01110,0b00100,0b11111,0b00100,0b00100,0b01010,0b10001])
    lcd_create_char(CHAR_STEVE_B, [0b01110,0b01110,0b00100,0b11111,0b00100,0b00100,0b10001,0b01010])
    lcd_create_char(CHAR_STEVE_J, [0b01110,0b01110,0b00100,0b11111,0b01110,0b01010,0b00000,0b00000])
    lcd_create_char(CHAR_CACTUS,  [0b00100,0b10101,0b10101,0b01110,0b00100,0b00100,0b00110,0b11111])
    lcd_create_char(CHAR_WALL,    [0b11111,0b10101,0b11111,0b10001,0b11111,0b10101,0b11111,0b10001])
    lcd_create_char(CHAR_BIRD,    [0b00000,0b01010,0b11111,0b01110,0b00100,0b00000,0b00000,0b00000])

def game_reset():
    global game_score, obstacle_x, obstacle_type, last_obstacle_move, is_jumping, jump_start, steve_frame
    game_score = 0
    obstacle_x = 15
    obstacle_type = 0
    last_obstacle_move = time.ticks_ms()
    is_jumping = False
    jump_start = 0
    steve_frame = 0

def game_draw(now):
    global steve_frame, last_steve_frame
    if time.ticks_diff(now, last_steve_frame) > STEVE_FRAME_MS:
        steve_frame = 1 - steve_frame
        last_steve_frame = now
    steve_char = CHAR_STEVE_J if is_jumping else (CHAR_STEVE_A if steve_frame == 0 else CHAR_STEVE_B)
    row0 = [32] * 16
    score_str = str(game_score)
    for i, c in enumerate(score_str):
        row0[16 - len(score_str) + i] = ord(c)
    if obstacle_type == 2 and 0 <= obstacle_x < 16:
        row0[obstacle_x] = CHAR_BIRD
    if is_jumping:
        row0[PLAYER_COL] = steve_char
    row1 = [32] * 16
    if not is_jumping:
        row1[PLAYER_COL] = steve_char
    if obstacle_type != 2 and 0 <= obstacle_x < 16:
        row1[obstacle_x] = CHAR_CACTUS if obstacle_type == 0 else CHAR_WALL
    lcd_set_cursor(0, 0)
    for b in row0: lcd_send_byte(b, LCD_RS)
    lcd_set_cursor(1, 0)
    for b in row1: lcd_send_byte(b, LCD_RS)

def game_update(now):
    global game_state, game_score, obstacle_x, obstacle_type, last_obstacle_move, is_jumping, jump_start, last_btn_press
    btn = game_btn.value()
    if game_state == GAME_IDLE:
        lcd_clear_print(0, " Running Steve!")
        lcd_clear_print(1, " Btn to start! ")
        if btn == 0 and time.ticks_diff(now, last_btn_press) > BTN_DEBOUNCE:
            last_btn_press = now
            game_reset()
            game_state = GAME_PLAYING
    elif game_state == GAME_PLAYING:
        if btn == 0 and not is_jumping and time.ticks_diff(now, last_btn_press) > BTN_DEBOUNCE:
            is_jumping = True
            jump_start = now
            last_btn_press = now
            beep(800, 30)
        if is_jumping and time.ticks_diff(now, jump_start) > JUMP_DURATION:
            is_jumping = False
        obstacle_speed = max(120, 280 - game_score * 8)
        if time.ticks_diff(now, last_obstacle_move) > obstacle_speed:
            obstacle_x -= 1
            last_obstacle_move = now
            if obstacle_x < 0:
                obstacle_x = 15
                game_score += 1
                obstacle_type = game_score % 3
        hit = False
        if obstacle_x == PLAYER_COL:
            if (obstacle_type == 2 and is_jumping) or (obstacle_type != 2 and not is_jumping):
                hit = True
        if hit:
            game_state = GAME_OVER
            beep(200, 400)
        game_draw(now)
    elif game_state == GAME_OVER:
        lcd_clear_print(0, " GAME OVER! ")
        lcd_clear_print(1, "Score: " + str(game_score))
        if btn == 0 and time.ticks_diff(now, last_btn_press) > BTN_DEBOUNCE:
            last_btn_press = now
            game_state = GAME_IDLE

# ====================== HELP ======================
def help_update(now):
    global help_index, last_joy_move
    if time.ticks_diff(now, last_joy_move) > JOY_DEBOUNCE:
        direction = joy_direction()
        if direction == "down":
            help_index = min(len(help_lines)-2, help_index + 1)
            last_joy_move = now
            beep(1200, 20)
        elif direction == "up":
            help_index = max(0, help_index - 1)
            last_joy_move = now
            beep(1000, 20)
    line1 = help_lines[help_index]
    line2 = help_lines[help_index + 1] if help_index + 1 < len(help_lines) else ""
    lcd_clear_print(0, line1)
    lcd_clear_print(1, line2)

# ====================== MESSAGING ======================
MSG_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789!?.,:-)"
MSG_MAX_LEN = 16
MSG_DEBOUNCE = 250
msg_text = ""
msg_char_idx = 0
msg_sent = ""
last_msg_input = 0
joy_btn_prev = 1
joy_btn_press_time = 0

def msg_send(now):
    global msg_text, msg_char_idx, msg_sent, last_msg_input, last_screen_content
    if len(msg_text) == 0:
        return
    if not wifi_connected:
        lcd_clear_print(0, "Not connected!")
        lcd_clear_print(1, "Go to WiFi scrn")
        beep(400, 300)
        time.sleep_ms(1500)
        return
    msg_sent = msg_text
    try:
        lcd_clear_print(0, "Connecting...")
        lcd_clear_print(1, PEER_IP)
        s = socket.socket()
        s.settimeout(4)
        s.connect((PEER_IP, MSG_PORT))
        s.sendall(msg_text.encode())
        s.close()
        lcd_clear_print(0, " Sent! ")
        lcd_clear_print(1, msg_sent[:14])
        beep(1800, 100)
    except Exception as e:
        lcd_clear_print(0, " Send failed! ")
        lcd_clear_print(1, str(e)[:16])
        print("Send error:", type(e), e)
        beep(400, 500)
    msg_text = ""
    msg_char_idx = 0
    last_screen_content = ("", "")
    last_msg_input = now

def compose_update(now):
    global msg_text, msg_char_idx, last_msg_input, joy_btn_prev, joy_btn_press_time
    joy_btn_cur = joy_btn.value()
    if joy_btn_cur == 0 and joy_btn_prev == 1:
        joy_btn_press_time = now
    elif joy_btn_cur == 1 and joy_btn_prev == 0:
        if time.ticks_diff(now, joy_btn_press_time) < 600:
            msg_send(now)
            joy_btn_prev = joy_btn_cur
            return
    joy_btn_prev = joy_btn_cur
    if time.ticks_diff(now, last_msg_input) >= MSG_DEBOUNCE:
        direction = joy_direction()
        if direction == "down":
            msg_char_idx = (msg_char_idx + 1) % len(MSG_CHARS)
            last_msg_input = now
            beep(1200, 20)
        elif direction == "up":
            msg_char_idx = (msg_char_idx - 1) % len(MSG_CHARS)
            last_msg_input = now
            beep(1000, 20)
    if game_btn.value() == 0 and time.ticks_diff(now, last_msg_input) > MSG_DEBOUNCE:
        if len(msg_text) < MSG_MAX_LEN:
            msg_text += MSG_CHARS[msg_char_idx]
            beep(1400, 30)
            last_msg_input = now
    if back_btn.value() == 0 and time.ticks_diff(now, last_msg_input) > MSG_DEBOUNCE:
        if len(msg_text) > 0:
            msg_text = msg_text[:-1]
            beep(600, 30)
            last_msg_input = now
    display_text = msg_text[-15:] if len(msg_text) > 15 else msg_text
    padding = " " * (15 - len(display_text))
    display_text = display_text + padding + MSG_CHARS[msg_char_idx]
    lcd_clear_print(0, display_text)
    lcd_clear_print(1, "U/D:ch OK:add")

def inbox_update(now):
    if msg_received == "":
        lcd_clear_print(0, " No messages ")
        lcd_clear_print(1, "Waiting...")
    else:
        lcd_clear_print(0, "From receiver:")
        lcd_clear_print(1, msg_received[:16])

# ====================== HELPERS ======================
def joy_direction():
    val_x = sum(joy_x.read_u16() for _ in range(16)) // 16
    val_y = sum(joy_y.read_u16() for _ in range(16)) // 16
    dx = val_x - JOY_CENTER_X
    dy = val_y - JOY_CENTER_Y
    if abs(dy) > abs(dx):
        if dy < -JOY_DEADZONE: return "down"
        elif dy > JOY_DEADZONE: return "up"
    else:
        if dx < -JOY_DEADZONE: return "left"
        elif dx > JOY_DEADZONE: return "right"
    return "none"

def set_lcd(line0, line1):
    global last_screen_content
    if last_screen_content == (line0, line1):
        return
    last_screen_content = (line0, line1)
    lcd_clear_print(0, line0)
    lcd_clear_print(1, line1)

def pad_center(text, width=16):
    spaces = (width - len(text)) // 2
    return " " * spaces + text

def bpm_to_color(b):
    if b < 75: return (0, 0, 180)
    elif b < 110: return (0, 180, 0)
    else: return (180, 0, 0)

def scale_color(color, brightness):
    return (int(color[0]*brightness), int(color[1]*brightness), int(color[2]*brightness))

def render_wave(progress, color):
    center = (NUM_LEDS - 1) / 2.0
    front = progress * (NUM_LEDS / 2.0 + 1)
    for i in range(NUM_LEDS):
        dist = abs(i - center)
        behind = front - dist
        if behind < 0:
            brightness = 0.0
        elif behind < 1.0:
            brightness = behind
        elif behind < 2.5:
            brightness = max(0.0, 1.0 - (behind - 1.0) / 1.5)
        else:
            brightness = 0.0
        leds[i] = scale_color(color, brightness)
    leds.write()

# ====================== WIFI SCREEN ======================
wifi_mode = "scan"

def wifi_update(now):
    global wifi_mode
    if wifi_mode == "scan":
        start_sta_mode()
        if wifi_connected:
            wifi_mode = "connected"
        else:
            wifi_mode = "failed"
    elif wifi_mode == "connected":
        lcd_clear_print(0, "WiFi Connected")
        lcd_clear_print(1, "Ready to send!")
    elif wifi_mode == "failed":
        lcd_clear_print(0, "Not connected")
        lcd_clear_print(1, "Check receiver")

# ====================== STARTUP ======================
time.sleep_ms(500)
lcd_init()
lcd_clear_print(0, " Heart Watch ")
lcd_clear_print(1, "  AP Sender  ")
time.sleep_ms(1500)
last_screen_content = ("", "")
print("Sender Ready - Go to WiFi screen to connect")

# ====================== MAIN LOOP ======================
while True:
    now = time.ticks_ms()
    check_incoming()

    raw = pulse.read_u16()
    if rolling_avg == 0:
        rolling_avg = raw
    else:
        alpha = AVG_ALPHA_FAST if rolling_avg < 20000 else AVG_ALPHA
        rolling_avg = (1 - alpha) * rolling_avg + alpha * raw

    has_finger = raw > 2000 or rolling_avg > 2000
    if has_finger:
        last_finger_seen = now
        if raw < 5000 and not in_beat:
            in_beat = True
            interval = time.ticks_diff(now, last_beat)
            if last_beat != 0 and MIN_INTERVAL < interval < MAX_INTERVAL:
                new_bpm = 60000 / interval
                bpm = (1 - BPM_ALPHA) * bpm + BPM_ALPHA * new_bpm
            last_beat = now
            wave_progress = 0.0
        elif raw > 10000:
            in_beat = False
    elif time.ticks_diff(now, last_finger_seen) > FINGER_TIMEOUT:
        in_beat = False
        last_beat = 0
        rolling_avg = 0
        bpm = 0.0

    if current_screen != SCREEN_WIFI:
        if time.ticks_diff(now, last_joy_move) > 100:
            direction = joy_direction()
            if direction != "none" and time.ticks_diff(now, last_joy_move) > JOY_DEBOUNCE:
                if direction == "right":
                    current_screen = (current_screen + 1) % NUM_SCREENS
                elif direction == "left":
                    current_screen = (current_screen - 1) % NUM_SCREENS
                if current_screen == SCREEN_HELP:
                    help_index = 0
                last_joy_move = now
                last_screen_content = ("", "")
                if current_screen == SCREEN_GAME:
                    game_load_chars()
                    game_state = GAME_IDLE
                if current_screen == SCREEN_WIFI:
                    wifi_mode = "scan"
            if prev_screen == SCREEN_GAME and current_screen != SCREEN_GAME:
                lcd_init()
                time.sleep_ms(50)
                last_screen_content = ("", "")
            prev_screen = current_screen

    if current_screen in (SCREEN_INBOX, SCREEN_COMPOSE, SCREEN_WIFI):
        if joy_btn.value() == 0:
            if joy_btn_held_start == 0:
                joy_btn_held_start = now
            elif time.ticks_diff(now, joy_btn_held_start) > 1000:
                joy_btn_held_start = 0
                current_screen = SCREEN_HEART
                last_screen_content = ("", "")
                lcd_init()
                time.sleep_ms(50)
                print("Exited to Heart screen via hold")
        else:
            joy_btn_held_start = 0

    if bpm > HIGH_BPM and current_screen != SCREEN_GAME:
        if time.ticks_diff(now, last_beep) > BEEP_INTERVAL:
            beep(1000, 100)
            last_beep = now
    else:
        speaker.duty_u16(0)

    if current_screen == SCREEN_GAME:
        game_update(now)
    elif current_screen == SCREEN_COMPOSE:
        compose_update(now)
    elif current_screen == SCREEN_INBOX:
        inbox_update(now)
    elif current_screen == SCREEN_WIFI:
        wifi_update(now)
    elif current_screen == SCREEN_HELP:
        help_update(now)
    elif time.ticks_diff(now, last_lcd_update) > LCD_UPDATE_MS:
        last_lcd_update = now
        if current_screen == SCREEN_HEART:
            if not has_finger:
                set_lcd(pad_center("Heart Rate"), pad_center("Place finger"))
            elif bpm == 0:
                set_lcd(pad_center("Heart Rate"), pad_center("Reading..."))
            else:
                set_lcd(pad_center("Heart Rate"), pad_center(str(int(bpm)) + " BPM"))
        elif current_screen == SCREEN_BATTERY:
            set_lcd(pad_center("Battery"), pad_center("3.70V 50%"))

    if not has_finger:
        breathe = ((now // 20) % 50) / 50.0
        c = scale_color((30, 30, 30), breathe)
        for i in range(NUM_LEDS):
            leds[i] = c
        leds.write()
    else:
        color = bpm_to_color(bpm)
        if wave_progress < 1.0:
            wave_progress = min(1.0, wave_progress + WAVE_SPEED)
            render_wave(wave_progress, color)
        else:
            for i in range(NUM_LEDS):
                leds[i] = (0, 0, 0)
            leds.write()

    time.sleep_ms(5)