from machine import ADC, Pin, I2C, PWM
import neopixel
import time
import network
import urequests

# --- Hardware setup ---
pulse = ADC(26)
leds = neopixel.NeoPixel(Pin(15), 8)
i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=400000)
speaker = PWM(Pin(2))
speaker.duty_u16(0)
joy_x = ADC(Pin(27))
joy_btn = Pin(3, Pin.IN, Pin.PULL_UP)
game_btn = Pin(4, Pin.IN, Pin.PULL_UP)

# --- LCD settings ---
LCD_ADDR = 0x27
LCD_BACKLIGHT = 0x08
LCD_ENABLE = 0x04
LCD_RS = 0x01

def lcd_write_byte(data):
    i2c.writeto(LCD_ADDR, bytes([data | LCD_BACKLIGHT]))

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
    time.sleep_ms(100)
    for _ in range(3):
        lcd_send_nibble(0x30, 0)
        time.sleep_ms(10)
    lcd_send_nibble(0x20, 0)
    time.sleep_ms(5)
    lcd_command(0x28)
    time.sleep_ms(1)
    lcd_command(0x08)
    time.sleep_ms(1)
    lcd_command(0x01)
    time.sleep_ms(5)
    lcd_command(0x06)
    time.sleep_ms(1)
    lcd_command(0x0C)
    time.sleep_ms(5)

def lcd_set_cursor(row, col):
    offsets = [0x00, 0x40]
    lcd_command(0x80 | (offsets[row] + col))

def lcd_print(text):
    for char in text:
        lcd_char(char)

def lcd_clear_print(row, text):
    text = text[:16]
    while len(text) < 16:
        text = text + " "
    lcd_set_cursor(row, 0)
    lcd_print(text)

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

# --- WiFi and time ---
WIFI_SSID = "BELL739"
WIFI_PASS = "DAFCC24A27E6"

time_base = 0
boot_ticks = 0

def connect_wifi():
    lcd_clear_print(0, "Connecting WiFi")
    lcd_clear_print(1, WIFI_SSID)
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(20):
        if wlan.isconnected():
            return True
        time.sleep(1)
    return False

def sync_time():
    global time_base, boot_ticks
    try:
        r = urequests.get("http://timeapi.io/api/time/current/zone?timeZone=America/Toronto")
        data = r.json()
        r.close()
        dt = data["dateTime"]
        h = int(dt[11:13])
        m = int(dt[14:16])
        s = int(dt[17:19])
        time_base = h * 3600 + m * 60 + s
        boot_ticks = time.ticks_ms()
        return True
    except:
        return False

def get_current_time():
    elapsed = time.ticks_diff(time.ticks_ms(), boot_ticks) // 1000
    secs = (time_base + elapsed) % 86400
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return h, m, s

def get_battery():
    vsys = ADC(29)
    raw = vsys.read_u16()
    voltage = raw * 3.3 / 65535 * 3
    pct = int((voltage - 3.0) / (4.2 - 3.0) * 100)
    pct = max(0, min(100, pct))
    return voltage, pct

# --- Pulse sensor settings ---
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
last_lcd_update = 0
LCD_UPDATE_MS = 500
last_beep = 0
BEEP_INTERVAL = 600
in_beat = False
rolling_avg = 0
AVG_ALPHA = 0.02
AVG_ALPHA_FAST = 0.15
FINGER_TIMEOUT = 2000

# --- Menu ---
SCREEN_TIME = 0
SCREEN_HEART = 1
SCREEN_BATTERY = 2
SCREEN_GAME = 3
NUM_SCREENS = 4
current_screen = SCREEN_HEART
prev_screen = -1

last_joy_move = 0
JOY_DEBOUNCE = 400
JOY_CENTER = 51000
JOY_DEADZONE = 10000

last_screen_content = ("", "")

# --- Game ---
GAME_IDLE = 0
GAME_PLAYING = 1
GAME_OVER = 2

game_state = GAME_IDLE
game_score = 0
obstacle_x = 15
last_obstacle_move = 0
is_jumping = False
jump_start = 0
last_btn_press = 0
BTN_DEBOUNCE = 200
PLAYER_COL = 1
JUMP_DURATION = 500
CHAR_PLAYER = 0
CHAR_OBSTACLE = 1

def game_load_chars():
    lcd_create_char(CHAR_PLAYER, [
        0b00100,
        0b01110,
        0b00100,
        0b01110,
        0b10101,
        0b00100,
        0b01010,
        0b01010
    ])
    lcd_create_char(CHAR_OBSTACLE, [
        0b00100,
        0b00100,
        0b10101,
        0b01110,
        0b00100,
        0b00100,
        0b00110,
        0b11111
    ])

def game_reset():
    global game_score, obstacle_x, last_obstacle_move, is_jumping, jump_start
    game_score = 0
    obstacle_x = 15
    last_obstacle_move = time.ticks_ms()
    is_jumping = False
    jump_start = 0

def game_draw():
    row0 = [32] * 16
    score_str = str(game_score)
    for i, c in enumerate(score_str):
        row0[16 - len(score_str) + i] = ord(c)
    if is_jumping:
        row0[PLAYER_COL] = CHAR_PLAYER
    row1 = [32] * 16
    if not is_jumping:
        row1[PLAYER_COL] = CHAR_PLAYER
    if 0 <= obstacle_x < 16:
        row1[obstacle_x] = CHAR_OBSTACLE
    lcd_set_cursor(0, 0)
    for b in row0:
        lcd_send_byte(b, LCD_RS)
    lcd_set_cursor(1, 0)
    for b in row1:
        lcd_send_byte(b, LCD_RS)

def game_update(now):
    global game_state, game_score, obstacle_x, last_obstacle_move
    global is_jumping, jump_start, last_btn_press
    btn = game_btn.value()  # dedicated game button on GP4
    if game_state == GAME_IDLE:
        lcd_clear_print(0, "  MEGA RUNNER  ")
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
        if is_jumping and time.ticks_diff(now, jump_start) > JUMP_DURATION:
            is_jumping = False
        obstacle_speed = max(120, 280 - game_score * 8)
        if time.ticks_diff(now, last_obstacle_move) > obstacle_speed:
            obstacle_x -= 1
            last_obstacle_move = now
            if obstacle_x < 0:
                obstacle_x = 15
                game_score += 1
        if obstacle_x == PLAYER_COL and not is_jumping:
            game_state = GAME_OVER
            beep(200, 400)
        game_draw()
    elif game_state == GAME_OVER:
        score_str = "Score: " + str(game_score)
        lcd_clear_print(0, "  GAME  OVER!  ")
        lcd_clear_print(1, score_str)
        if btn == 0 and time.ticks_diff(now, last_btn_press) > BTN_DEBOUNCE:
            last_btn_press = now
            game_state = GAME_IDLE

def joy_direction():
    val = sum(joy_x.read_u16() for _ in range(16)) // 16
    if val < JOY_CENTER - JOY_DEADZONE:
        return "left"
    elif val > JOY_CENTER + JOY_DEADZONE:
        return "right"
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
    if b < 75:    return (0, 0, 180)
    elif b < 110: return (0, 180, 0)
    else:         return (180, 0, 0)

def scale_color(color, brightness):
    return (int(color[0]*brightness), int(color[1]*brightness), int(color[2]*brightness))

def render_wave(progress, color):
    center = (NUM_LEDS - 1) / 2.0
    front = progress * (NUM_LEDS / 2.0 + 1)
    for i in range(NUM_LEDS):
        dist = abs(i - center)
        behind = front - dist
        if behind < 0:       brightness = 0.0
        elif behind < 1.0:   brightness = behind
        elif behind < 2.5:   brightness = max(0.0, 1.0 - (behind - 1.0) / 1.5)
        else:                brightness = 0.0
        leds[i] = scale_color(color, brightness)
    leds.write()

# --- Startup ---
time.sleep_ms(500)
lcd_init()
time.sleep_ms(100)
lcd_clear_print(0, "  Heart Watch  ")
lcd_clear_print(1, "  Starting...  ")
time.sleep_ms(1000)

if connect_wifi():
    lcd_clear_print(0, "WiFi connected!")
    lcd_clear_print(1, "Syncing time...")
    time.sleep_ms(500)
    if sync_time():
        h, m, s = get_current_time()
        lcd_clear_print(0, "Time synced!   ")
        lcd_clear_print(1, "%02d:%02d:%02d" % (h, m, s))
    else:
        lcd_clear_print(1, "Time sync fail ")
    time.sleep_ms(1000)
else:
    lcd_clear_print(0, "WiFi failed    ")
    lcd_clear_print(1, "No time sync   ")
    time.sleep_ms(1000)

lcd_init()
time.sleep_ms(100)
last_screen_content = ("", "")
print("Ready")

# --- Main loop ---
while True:
    now = time.ticks_ms()

    # --- Pulse sensor ---
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
                print(f"BPM: {int(bpm)}")
            last_beat = now
            wave_progress = 0.0
        elif raw > 10000:
            in_beat = False
    else:
        if time.ticks_diff(now, last_finger_seen) > FINGER_TIMEOUT:
            in_beat = False
            last_beat = 0
            rolling_avg = 0
            bpm = 0.0

    # --- Joystick navigation ---
    if time.ticks_diff(now, last_joy_move) > 100:
        direction = joy_direction()
        if direction != "none" and time.ticks_diff(now, last_joy_move) > JOY_DEBOUNCE:
            if direction == "right":
                current_screen = (current_screen + 1) % NUM_SCREENS
            elif direction == "left":
                current_screen = (current_screen - 1) % NUM_SCREENS
            last_joy_move = now
            last_screen_content = ("", "")
            if current_screen == SCREEN_GAME:
                game_load_chars()
                game_state = GAME_IDLE
            if prev_screen == SCREEN_GAME and current_screen != SCREEN_GAME:
                lcd_init()
                time.sleep_ms(50)
                last_screen_content = ("", "")
            prev_screen = current_screen

    # --- Alarm ---
    if bpm > HIGH_BPM and current_screen != SCREEN_GAME:
        if time.ticks_diff(now, last_beep) > BEEP_INTERVAL:
            beep(1000, 100)
            last_beep = now
    else:
        speaker.duty_u16(0)

    # --- LCD update ---
    if current_screen == SCREEN_GAME:
        game_update(now)

    elif time.ticks_diff(now, last_lcd_update) > LCD_UPDATE_MS:
        last_lcd_update = now

        if current_screen == SCREEN_TIME:
            if time_base > 0:
                h, m, s = get_current_time()
                set_lcd(
                    pad_center("Current Time"),
                    pad_center("%02d:%02d:%02d" % (h, m, s))
                )
            else:
                set_lcd(pad_center("Current Time"), pad_center("No WiFi sync"))

        elif current_screen == SCREEN_HEART:
            if not has_finger:
                set_lcd(pad_center("Heart Rate"), pad_center("Place finger"))
            elif bpm == 0:
                set_lcd(pad_center("Heart Rate"), pad_center("Reading..."))
            else:
                set_lcd(pad_center("Heart Rate"), pad_center(str(int(bpm)) + " BPM"))

        elif current_screen == SCREEN_BATTERY:
            voltage, pct = get_battery()
            v_str = "%.2fV" % voltage
            set_lcd(pad_center("Battery"), pad_center(v_str + " " + str(pct) + "%"))

    # --- LEDs ---
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