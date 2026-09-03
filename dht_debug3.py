import sys
sys.path.insert(0, ".")
import os, time

DHT11_GPIO = 111
GPIO_BASE = "/sys/class/gpio"
GPIO_PIN_DIR = f"{GPIO_BASE}/gpio{DHT11_GPIO}"

def gpio_export():
    if os.path.isdir(GPIO_PIN_DIR):
        return
    with open(f"{GPIO_BASE}/export", "w") as fp:
        fp.write(str(DHT11_GPIO))

def gpio_set_direction(d):
    with open(f"{GPIO_PIN_DIR}/direction", "w") as fp:
        fp.write(d)

def gpio_write(v):
    with open(f"{GPIO_PIN_DIR}/value", "w") as fp:
        fp.write("1" if v else "0")

gpio_export()
gpio_set_direction("out")
gpio_write(1)
time.sleep(0.05)
gpio_write(0)
time.sleep(0.018)
gpio_write(1)
time.sleep(0.00003)
gpio_set_direction("in")

fd = os.open(GPIO_PIN_DIR + "/value", os.O_RDONLY)

def read_level():
    os.lseek(fd, 0, os.SEEK_SET)
    return 1 if os.read(fd, 8).strip() == b"1" else 0

last_state = read_level()
transition_counts = []
MAX_TRANSITIONS = 90
LOOP_TIMEOUT = 20000

for i in range(MAX_TRANSITIONS):
    count = 0
    timed_out = False
    while read_level() == last_state:
        count += 1
        if count >= LOOP_TIMEOUT:
            timed_out = True
            break
    transition_counts.append((last_state, count, timed_out))
    if timed_out:
        break
    last_state = 1 - last_state

os.close(fd)
print("Total records:", len(transition_counts))
for rec in transition_counts:
    print(rec)

