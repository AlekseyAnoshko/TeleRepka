import sys
sys.path.insert(0, ".")
import sensors_gpiod_test as s
import time

chip = s.gpiod.Chip(s.DHT11_GPIOCHIP)
line = chip.get_line(s.DHT11_LINE_OFFSET)

line.request(consumer="dht11dbg", type=s.gpiod.LINE_REQ_DIR_OUT, default_val=1)
time.sleep(0.05)
line.set_value(0)
time.sleep(0.020)
line.set_value(1)
s._busy_wait_us(30)
line.release()

line.request(consumer="dht11dbg", type=s.gpiod.LINE_REQ_DIR_IN)

def read_level():
    return line.get_value()

initial = read_level()
print("initial:", initial)

records = []
last_state = initial
MAX_T = 90
TIMEOUT = 20000
for i in range(MAX_T):
    count = 0
    timed_out = False
    while read_level() == last_state:
        count += 1
        if count >= TIMEOUT:
            timed_out = True
            break
    records.append((last_state, count, timed_out))
    if timed_out:
        break
    last_state = 1 - last_state

line.release()
chip.close()

print("Total records:", len(records))
for r in records:
    print(r)
