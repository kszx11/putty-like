import sys
import threading
import serial
import time
import sys

# Platform-specific imports for immediate char input
if sys.platform.startswith('win'):
    import msvcrt
else:
    import tty
    import termios

def read_serial(ser):
    """Continuously read from serial and print output."""
    try:
        while True:
            try:
                n = ser.in_waiting or 1
                data = ser.read(n)
            except AttributeError:
                data = ser.read(1)
            if data:
                try:
                    sys.stdout.write(data.decode('utf-8', errors='replace'))
                except Exception:
                    sys.stdout.write(str(data))
                sys.stdout.flush()
            else:
                time.sleep(0.01)
    except serial.SerialException:
        print("\nSerial port closed.")
    except Exception as e:
        print(f"\nError reading serial port: {e}")

def get_char():
    """Read one character from stdin without waiting for Enter."""
    if sys.platform.startswith('win'):
        ch = msvcrt.getwch()
        # Extended/function keys on Windows: prefix 0x00 or 0xe0
        if ch in ('\x00', '\xe0'):
            ch2 = msvcrt.getwch()
            code = ord(ch2)
            # Map function keys F1-F12 to ANSI escape sequences
            ext_map = {
                59: '\x1bOP',   # F1
                60: '\x1bOQ',   # F2
                61: '\x1bOR',   # F3
                62: '\x1bOS',   # F4
                63: '\x1b[15~', # F5
                64: '\x1b[17~', # F6
                65: '\x1b[18~', # F7
                66: '\x1b[19~', # F8
                67: '\x1b[20~', # F9
                68: '\x1b[21~', # F10
                133: '\x1b[23~',# F11
                134: '\x1b[24~', # F12
                # Navigation and control keys
                72: '\x1b[A',    # Up Arrow
                80: '\x1b[B',    # Down Arrow
                75: '\x1b[D',    # Left Arrow
                77: '\x1b[C',    # Right Arrow
                71: '\x1b[1~',   # Home
                79: '\x1b[4~',   # End
                73: '\x1b[5~',   # Page Up
                81: '\x1b[6~',   # Page Down
                82: '\x1b[2~',   # Insert
                83: '\x1b[3~'    # Delete
            }
            seq = ext_map.get(code)
            if seq:
                return seq
            return ''
        # Handle Ctrl-C
        if ch == '\x03':
            raise KeyboardInterrupt
        # Normalize Enter
        return '\n' if ch == '\r' else ch
    # Unix / Linux / Mac
    ch = sys.stdin.read(1)
    # Handle Ctrl-C
    if ch == '\x03':
        raise KeyboardInterrupt
    return '\n' if ch == '\r' else ch

def main():
    if len(sys.argv) < 2:
        print("Usage: python putty_like.py <port> [baudrate] [lineending] [rtscts] [xonxoff]")
        print("  lineending: CR / LF / CRLF (default LF)")
        print("  rtscts: 0 or 1 (default 0)")
        print("  xonxoff: 0 or 1 (default 0)")
        sys.exit(1)

    port = sys.argv[1]
    baudrate = int(sys.argv[2]) if len(sys.argv) >= 3 else 115200
    lineending = sys.argv[3].upper() if len(sys.argv) >= 4 else 'LF'
    rtscts = sys.argv[4] == '1' if len(sys.argv) >= 5 else False
    xonxoff = sys.argv[5] == '1' if len(sys.argv) >= 6 else False

    # Determine the line ending bytes to append on Enter
    if lineending == 'CR':
        line_ending_bytes = b'\r'
    elif lineending == 'CRLF':
        line_ending_bytes = b'\r\n'
    else:
        line_ending_bytes = b'\n'  # default LF

    try:
        ser = serial.Serial(port, baudrate, timeout=0,
                            rtscts=rtscts, xonxoff=xonxoff)

    except serial.SerialException as e:
        print(f"Failed to open serial port {port}: {e}")
        sys.exit(1)

    print(f"Connected to {port} at {baudrate} baud.")
    print(f"Line ending: {lineending}, RTS/CTS: {rtscts}, XON/XOFF: {xonxoff}")
    print("Press Ctrl+C to quit.")
    # Configure terminal for single-character input on Unix
    is_win = sys.platform.startswith('win')
    if not is_win:
        fd = sys.stdin.fileno()
        old_term_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)

    read_thread = threading.Thread(target=read_serial, args=(ser,), daemon=True)
    read_thread.start()

    try:
        while True:
            ch = get_char()
            # Backspace support
            if ch in ('\x7f','\b'):
                ser.write(b'\x08')
                sys.stdout.write('\b \b')
                sys.stdout.flush()
                continue
            # Enter key: send line ending and echo newline
            if ch == '\n':
                ser.write(line_ending_bytes)
                sys.stdout.write('\n')
                sys.stdout.flush()
            else:
                # send character (echo handled by remote)
                b = ch.encode('ascii','ignore')
                ser.write(b)

    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        # Restore terminal settings on Unix
        if not sys.platform.startswith('win'):
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_term_settings)
            except Exception:
                pass
        ser.close()

if __name__ == "__main__":
    main()

# Explanation:
# - Input is read one char at a time and immediately sent to the serial port.
# - Enter key sends the configured line ending (`CR`, `LF`, or `CRLF`).
# - Serial input from the Pico is printed as soon as it arrives.
# - Flow control (`rtscts` and `xonxoff`) can be toggled via command line.
# - Works on Windows and Unix-like OSes.
#
# Notes
# - On Windows, `msvcrt.getwch()` reads Unicode characters without waiting for Enter.
#     - On Linux/macOS, terminal is switched to raw mode temporarily for single-char reads.
#     - This program runs in console/terminal, not GUI.
#
# Usage examples
# Connect to Pico on COM3 at 115200, default LF line ending, no flow control:
# bash
# python putty_like.py COM3 115200
#
# Connect on `/dev/ttyUSB0` at 115200, CRLF endings, RTS/CTS enabled, no XON/XOFF:
# bash
# python putty_like.py /dev/ttyUSB0 115200 CRLF 1 0
