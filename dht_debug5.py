import sys
sys.path.insert(0, ".")
import sensors, os, time

if not sensors._gpio_export():
    print("EXPORT FAILED")
    sys.exit(1)
if not sensors._gpio_set_direction("high"):
    print("SET DIRECTION HIGH FAILED")
    sys.exit(1)
time.sleep(0.05)
sensors._gpio_write(0)
time.sleep(0.020)
sensors._gpio_write(1)
sensors._busy_wait_us(30)
if not sensors._gpio_set_direction("in"):
    print("SET DIRECTION IN FAILED")
    sys.exit(1)

fd = os.open(sensors.GPIO_VALUE_PATH, os.O_RDONLY)

def read_level():
    os.lseek(fd, 0, os.SEEK_SET)
    return 1 if os.read(fd, 8).strip() == b"1" else 0

initial = read_level()
print("Initial level right after switching to input:", initial)

if initial == 1:
    c = sensors._wait_while_level(read_level, 1)
    print("Waited out initial HIGH, iterations:", c)
    if c is None:
        print("TIMEOUT waiting out initial HIGH")
        os.close(fd)
        sys.exit(1)

c_low = sensors._wait_while_level(read_level, 0)
print("Response LOW duration (iterations):", c_low)
if c_low is None:
    print("TIMEOUT on response LOW")
    os.close(fd)
    sys.exit(1)

c_high = sensors._wait_while_level(read_level, 1)
print("Response HIGH (preamble) duration (iterations):", c_high)
if c_high is None:
    print("TIMEOUT on response HIGH preamble")
    os.close(fd)
    sys.exit(1)

high_counts = []
for i in range(40):
    c_sep = sensors._wait_while_level(read_level, 0)
    if c_sep is None:
        print(f"TIMEOUT waiting LOW-separator at bit {i}")
        break
    c_bit = sensors._wait_while_level(read_level, 1)
    if c_bit is None:
        print(f"TIMEOUT waiting HIGH-bit at bit {i}")
        break
    high_counts.append(c_bit)
    print(i, "sep=", c_sep, "high=", c_bit)

os.close(fd)
print("Total bits captured:", len(high_counts))
