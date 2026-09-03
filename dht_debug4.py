import sys
sys.path.insert(0, ".")
import sensors, os, time

sensors._gpio_export()
sensors._gpio_set_direction("out")
sensors._gpio_write(1)
time.sleep(0.05)
sensors._gpio_write(0)
time.sleep(0.018)
sensors._gpio_write(1)
sensors._busy_wait_us(30)
sensors._gpio_set_direction("in")

fd = os.open(sensors.GPIO_PIN_DIR + "/value", os.O_RDONLY)

def read_level():
    os.lseek(fd, 0, os.SEEK_SET)
    return 1 if os.read(fd, 8).strip() == b"1" else 0

last_state = read_level()
records = []
MAX_TRANSITIONS = 90
LOOP_TIMEOUT = 20000

t0 = time.perf_counter()
for i in range(MAX_TRANSITIONS):
    count = 0
    timed_out = False
    while read_level() == last_state:
        count += 1
        if count >= LOOP_TIMEOUT:
            timed_out = True
            break
    t_now = time.perf_counter()
    records.append((last_state, count, round((t_now-t0)*1_000_000), timed_out))
    if timed_out:
        break
    last_state = 1 - last_state

os.close(fd)
print("Total records:", len(records))
for rec in records:
    print(rec)
