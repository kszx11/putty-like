import sys
import threading
import time
import socket
import select

# Optional serial support
try:
    import serial
    SerialException = serial.SerialException
except ImportError:
    serial = None
    SerialException = Exception

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
            # Poll socket or serial port
            if getattr(ser, 'telnet', False) and hasattr(ser, 'sock'):
                # Wait briefly for socket data or buffered data
                rlist, _, _ = select.select([ser.sock], [], [], 0.1)
                # If no new socket data and no buffered Telnet data, retry
                if not rlist and not ser.in_waiting:
                    continue
                data = ser.read(4096)
            else:
                try:
                    n = ser.in_waiting
                except Exception:
                    n = 1
                data = ser.read(n or 1)
                if not data:
                    time.sleep(0.01)
                    continue
            # Process received data
            if getattr(ser, 'telnet', False):
                # Normalize newlines: lone LF->CRLF, skip CR
                outbuf = bytearray()
                for b in data:
                    if b == 10:  # LF
                        outbuf += b'\r\n'
                    elif b == 13:  # CR
                        continue
                    else:
                        outbuf.append(b)
                try:
                    sys.stdout.buffer.write(outbuf)
                    sys.stdout.buffer.flush()
                except Exception:
                    try:
                        text = outbuf.decode('utf-8', errors='replace')
                        sys.stdout.write(text)
                        sys.stdout.flush()
                    except Exception:
                        pass
            else:
                # Serial mode: decode and write text
                try:
                    text = data.decode('utf-8', errors='replace')
                except Exception:
                    text = ''.join(chr(b) for b in data)
                sys.stdout.write(text)
                sys.stdout.flush()
    except SerialException:
        print("\nConnection closed.")
    except Exception as e:
        print(f"\nError reading port: {e}")

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

class SockWrapper:
    """Wrap a socket to mimic a serial.Serial-like interface with minimal Telnet negotiation support."""
    def __init__(self, sock, telnet=False):
        self.sock = sock
        self.telnet = telnet
        self._buf = b''
        if self.telnet:
            # Initial Telnet negotiation: disable local echo, request suppress go-ahead
            try:
                # IAC WONT ECHO (client will not echo, server should handle echo)
                self.sock.send(bytes([255, 252, 1]))
                # IAC DO SUPPRESS GO AHEAD (request server suppress go-ahead)
                self.sock.send(bytes([255, 253, 3]))
            except Exception:
                pass
    def write(self, data):
        return self.sock.send(data)
    @property
    def in_waiting(self):
        # If buffered data from telnet negotiation, report it; else assume data may arrive
        if self.telnet and len(self._buf) > 0:
            return len(self._buf)
        return 1
    def read(self, size=1):
        if not self.telnet:
            try:
                return self.sock.recv(size)
            except Exception:
                return b''
        # Telnet mode: handle negotiation
        # Read raw data
        try:
            data = self.sock.recv(4096)
        except Exception:
            data = b''
        if data:
            self._buf += data
        out = bytearray()
        # Strip Telnet IAC sequences and collect up to 'size' data bytes
        while len(out) < size and self._buf:
            b0 = self._buf[0]
            # Normal byte
            if b0 != 255:
                out.append(b0)
                self._buf = self._buf[1:]
                continue
            # IAC command
            if len(self._buf) < 2:
                break  # wait for more
            cmd = self._buf[1]
            # Literal 255
            if cmd == 255:
                out.append(255)
                self._buf = self._buf[2:]
                continue
            # Simple commands (no option): NOP, DM, BRK, etc.
            if cmd in (241,242,243,244,245,246,247,248,249):
                self._buf = self._buf[2:]
                continue
            # DO, DONT, WILL, WONT
            if cmd in (253, 254, 251, 252) and len(self._buf) >= 3:
                opt = self._buf[2]
                if cmd == 253:  # DO
                    # Server asks us to enable option
                    if opt in (1, 3):  # ECHO or SGA
                        self.sock.send(bytes([255, 251, opt]))  # WILL
                    else:
                        self.sock.send(bytes([255, 252, opt]))  # WONT
                elif cmd == 251:  # WILL
                    # Server will enable option
                    if opt == 3:  # SGA
                        self.sock.send(bytes([255, 253, opt]))  # DO
                    else:
                        self.sock.send(bytes([255, 254, opt]))  # DONT
                elif cmd == 254:  # DONT
                    self.sock.send(bytes([255, 252, opt]))  # WONT
                # WONT -> no action
                # Skip IAC, cmd, opt
                self._buf = self._buf[3:]
                continue
            # Subnegotiation
            if cmd == 250:  # SB
                # find IAC SE (255,240)
                end = -1
                for i in range(2, len(self._buf)-1):
                    if self._buf[i] == 255 and self._buf[i+1] == 240:
                        end = i
                        break
                if end >= 0:
                    # drop SB through SE
                    self._buf = self._buf[end+2:]
                    continue
                break
            # SE alone
            if cmd == 240:
                self._buf = self._buf[2:]
                continue
            # Unknown IAC, skip the IAC byte
            self._buf = self._buf[1:]
        return bytes(out)
    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

def main():
    # Determine mode: serial or telnet
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python putty_like.py <COM port> [baudrate] [lineending] [rtscts] [xonxoff]")
        print("  python putty_like.py telnet <host> [port]")
        sys.exit(1)

    mode = sys.argv[1].lower()
    if mode == 'telnet':
        if len(sys.argv) < 3:
            print("Usage: python putty_like.py telnet <host> [port]")
            sys.exit(1)
        host = sys.argv[2]
        telnet_port = int(sys.argv[3]) if len(sys.argv) >= 4 else 23
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, telnet_port))
            sock.setblocking(False)
            ser = SockWrapper(sock, telnet=True)
        except Exception as e:
            print(f"Telnet connect failed: {e}")
            sys.exit(1)
        print(f"Connected to telnet {host}:{telnet_port}.")
        # Default line ending for Enter key
        line_ending_bytes = b'\r'
        print("Press Ctrl+C to quit.")
    else:
        # Ensure serial module is available before using serial mode
        if serial is None:
            print("pyserial module is required for serial connections. Please install pyserial.")
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
        except SerialException as e:
            print(f"Failed to open serial port {port}: {e}")
            sys.exit(1)

        print(f"Connected to {port} at {baudrate} baud.")
        print(f"Line ending: {lineending}, RTS/CTS: {rtscts}, XON/XOFF: {xonxoff}")
        print("Press Ctrl+C to quit.")
    # Configure terminal for single-character input on Unix
    is_win = sys.platform.startswith('win')
    if not is_win:
        fd = sys.stdin.fileno()
        # save original terminal settings
        old_term_settings = termios.tcgetattr(fd)
        # set cbreak mode (character-at-a-time)
        tty.setcbreak(fd)
        # disable local echo
        new_settings = termios.tcgetattr(fd)
        new_settings[3] = new_settings[3] & ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSADRAIN, new_settings)

    read_thread = threading.Thread(target=read_serial, args=(ser,), daemon=True)
    read_thread.start()

    try:
        while True:
            ch = get_char()
            # Backspace support (send and locally erase)
            if ch in ('\x7f', '\b'):
                ser.write(b'\x08')
                sys.stdout.write('\b \b')
                sys.stdout.flush()
                continue
            # Enter key: send line ending
            if ch == '\n':
                ser.write(line_ending_bytes)
                # echo newline for serial only
                if not getattr(ser, 'telnet', False):
                    sys.stdout.write('\n')
                    sys.stdout.flush()
                continue
            # Normal character: send only; rely on server echo in telnet mode
            b = ch.encode('ascii', 'ignore')
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

# python putty_like.py <COM port> [baudrate] [lineending] [rtscts] [xonxoff]
# python putty_like.py telnet <host> [port]
