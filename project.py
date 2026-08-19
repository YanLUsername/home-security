import os
import sys
import time
import datetime
import smtplib
from email.mime.text import MIMEText
import RPi.GPIO as GPIO
import smbus

# GPIO Pins
BUTTON_GREEN = 17
BUTTON_BLUE = 25
BUTTON_RED = 26
LED_NORMAL = 27
LED_ALARM = 22
TRIG_PIN = 23
ECHO_PIN = 24
# I2C Addresses
ADC_ADDR = 0x4b
LCD_ADDR = 0x27
# System Modes
STATE_STARTUP = 0
STATE_NORMAL = 1
STATE_MENU = 2
STATE_SECURITY = 3
current_state = STATE_STARTUP
state_changed = True
# Security Thresholds
DISTANCE_THRESHOLD_CM = 30.0  # Trigger alarm if object is closer than 30cm/1ft
LIGHT_CHANGE_THRESHOLD = 40    # Trigger alarm if light deviates by this ADC step value
# Email Postfix
bus = smbus.SMBus(1)
email_recipient = "yanl72@uci.edu"
security_baseline_light = 0
email_cooldown_timer = 0

# LCD and ADC
# LCD Constants
LCD_WIDTH = 16
LCD_CHR = 1  # Sending data
LCD_CMD = 0  # Sending command
LCD_LINE_1 = 0x80
LCD_LINE_2 = 0xC0
LCD_BACKLIGHT = 0x08

def lcd_toggle_enable(bits):
    time.sleep(0.0005)
    bus.write_byte(LCD_ADDR, (bits | 0x04))
    time.sleep(0.0005)
    bus.write_byte(LCD_ADDR, (bits & ~0x04))
    time.sleep(0.0005)

def lcd_send_byte(bits, mode):
    bits_high = mode | (bits & 0xF0) | LCD_BACKLIGHT
    bits_low = mode | ((bits << 4) & 0xF0) | LCD_BACKLIGHT
    bus.write_byte(LCD_ADDR, bits_high)
    lcd_toggle_enable(bits_high)
    bus.write_byte(LCD_ADDR, bits_low)
    lcd_toggle_enable(bits_low)

def lcd_init():
    lcd_send_byte(0x33, LCD_CMD)
    lcd_send_byte(0x32, LCD_CMD)
    lcd_send_byte(0x06, LCD_CMD)
    lcd_send_byte(0x0C, LCD_CMD)
    lcd_send_byte(0x28, LCD_CMD)
    lcd_send_byte(0x01, LCD_CMD)
    time.sleep(0.005)

def lcd_display_string(message, line):
    message = message.ljust(LCD_WIDTH, " ")
    lcd_send_byte(line, LCD_CMD)
    for i in range(LCD_WIDTH):
        lcd_send_byte(ord(message[i]), LCD_CHR)

def read_adc(channel):
    ads7830_commands = [0x84, 0xC4, 0x94, 0xD4, 0xA4, 0xE4, 0xB4, 0xF4]
    bus.write_byte(ADC_ADDR, ads7830_commands[channel])
    return bus.read_byte(ADC_ADDR)

# Sensors
def get_temperature():
    raw_val = read_adc(0) # A0
    celsius = (raw_val / 255.0) * 50.0
    return round(celsius, 1)

def get_light_level():
    # Reads LDR (0-255)
    return read_adc(1) # A1

def get_ultrasonic_distance():
    GPIO.output(TRIG_PIN, True)
    time.sleep(0.00001)
    GPIO.output(TRIG_PIN, False)
    start_time = time.time()
    stop_time = time.time()
    timeout = start_time + 0.04

    while GPIO.input(ECHO_PIN) == 0:
        start_time = time.time()
        if start_time > timeout:
            return 999.0
    while GPIO.input(ECHO_PIN) == 1:
        stop_time = time.time()
        if stop_time > timeout:
            return 999.0

    elapsed_time = stop_time - start_time
    distance = (elapsed_time * 34300) / 2
    return round(distance, 1)

# Email
def send_security_alert(event_type, distance, light_val, current_time):
    global email_cooldown_timer
    # Emails every 30 seconds to prevent spam
    if time.time() < email_cooldown_timer:
        return

    print(f"[Action] Dispatching Alert Email for: {event_type}...")
    subject = "Raspberry Pi Security Alert"
    body = f"""Security Alert!
Mode: Security Mode
Event: {event_type}
Ultrasonic distance: {distance} cm
Light sensor value: {light_val}
Time: {current_time}"""

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = "smart-home@raspberrypi.local"
    msg['To'] = email_recipient

    server = smtplib.SMTP('localhost', 25)
    server.sendmail(msg['From'], [msg['To']], msg.as_string())
    server.quit()
    print("[Success] Email alert delivered successfully.")
    email_cooldown_timer = time.time() + 30

# Mode Management
def button_pressed_callback(channel):
    global current_state, state_changed, security_baseline_light
    time.sleep(0.05)
    if GPIO.input(channel) == GPIO.HIGH:
        return

    # Startup Menu
    if current_state == STATE_STARTUP:
        if channel == BUTTON_GREEN:
            current_state = STATE_NORMAL
            state_changed = True

    elif current_state == STATE_NORMAL:
        if channel == BUTTON_BLUE: # Press blue to enter menu
            current_state = STATE_MENU
            state_changed = True

    elif current_state == STATE_MENU:
        if channel == BUTTON_GREEN:
            current_state = STATE_NORMAL
            state_changed = True
        elif channel == BUTTON_RED:
            current_state = STATE_SECURITY
            security_baseline_light = get_light_level()
            state_changed = True

    elif current_state == STATE_SECURITY:
        if channel == BUTTON_BLUE: # Press blue to enter menu
            current_state = STATE_MENU
            state_changed = True

def update_lcd_display(line1, line2):
    lcd_display_string(line1, LCD_LINE_1)
    lcd_display_string(line2, LCD_LINE_2)

# Main
def run_normal_mode():
    GPIO.output(LED_NORMAL, GPIO.HIGH)
    GPIO.output(LED_ALARM, GPIO.LOW)

    # Update time to LCD
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")
    # Update temp to LCD
    temp = get_temperature()
    update_lcd_display(f"NORM Temp: {temp}C", now_str)
    time.sleep(1.0) # Delay between updates

def run_security_mode():
    GPIO.output(LED_NORMAL, GPIO.LOW) # Green LED off
    distance = get_ultrasonic_distance()
    light_level = get_light_level()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")
    update_lcd_display("SECURITY MODE", "Monitoring")

    # Threshold Parameters
    motion_breached = distance < DISTANCE_THRESHOLD_CM
    light_breached = abs(light_level - security_baseline_light) > LIGHT_CHANGE_THRESHOLD

    if motion_breached or light_breached:
        if motion_breached:
            event_msg = "Motion detected"
            update_lcd_display("ALERT!", f"Motion: {distance}cm")
        else: # Light breached
            event_msg = "Light anomaly"
            update_lcd_display("ALERT!", f"Light: {light_level}")

        print(f"\n[ALERT] {event_msg} at {now_str}!")
        print(f"Distance: {distance} cm | Light ADC Value: {light_level} (Baseline: {security_baseline_light})")

	# Send email and blink red LED
        send_security_alert(event_msg, distance, light_level, now_str)
        for x in range(3):
            GPIO.output(LED_ALARM, GPIO.HIGH)
            time.sleep(0.5)
            GPIO.output(LED_ALARM, GPIO.LOW)
            time.sleep(0.5)
    else:
        GPIO.output(LED_ALARM, GPIO.LOW)

    time.sleep(0.2)

def main():
    global state_changed
    # Setup Pins
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_GREEN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BUTTON_BLUE, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BUTTON_RED, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(LED_NORMAL, GPIO.OUT)
    GPIO.setup(LED_ALARM, GPIO.OUT)
    GPIO.setup(TRIG_PIN, GPIO.OUT)
    GPIO.setup(ECHO_PIN, GPIO.IN)

    GPIO.output(TRIG_PIN, False)
    lcd_init()

    print("System Activated") # Print to terminal
    GPIO.add_event_detect(BUTTON_GREEN, GPIO.FALLING, callback=button_pressed_callback, bouncetime=200)
    GPIO.add_event_detect(BUTTON_BLUE, GPIO.FALLING, callback=button_pressed_callback, bouncetime=200)
    GPIO.add_event_detect(BUTTON_RED, GPIO.FALLING, callback=button_pressed_callback, bouncetime=200)

    try:
        while True:
            if current_state == STATE_STARTUP:
                if state_changed:
                    GPIO.output(LED_NORMAL, GPIO.LOW)
                    GPIO.output(LED_ALARM, GPIO.LOW)
                    update_lcd_display("Welcome Home!", "Press Green")
                    state_changed = False
                time.sleep(0.1)

            elif current_state == STATE_NORMAL:
                run_normal_mode()

            elif current_state == STATE_MENU:
                if state_changed:
                    GPIO.output(LED_NORMAL, GPIO.LOW)
                    update_lcd_display("Grn:Normal Mode", "Red:SecurityMode")
                    state_changed = False
                time.sleep(0.1)

            elif current_state == STATE_SECURITY:
                run_security_mode()
    except KeyboardInterrupt:
        print("\n[System] Shutting Down")
    finally:
        update_lcd_display("System Offline", "")
        GPIO.cleanup()

if __name__ == "__main__":
    main()
