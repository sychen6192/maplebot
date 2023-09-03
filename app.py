from recovery import checkmana
from sss import *
import threading
import datetime
import time
from keyboard import PressKey, ReleaseKey, key_dict


def teleport(direction="L"):
    if direction == "L":
        PressKey(int(key_dict["DIK_NUMPAD4"], 16))
        time.sleep(0.5)
        PressKey(int(key_dict["DIK_7"], 16))
        time.sleep(0.5)
        ReleaseKey(int(key_dict["DIK_7"], 16))
        time.sleep(0.5)
        PressKey(int(key_dict["DIK_7"], 16))
        time.sleep(0.5)
        ReleaseKey(int(key_dict["DIK_7"], 16))
        ReleaseKey(int(key_dict["DIK_NUMPAD4"], 16))

    elif direction == "R":
        PressKey(int(key_dict["DIK_NUMPAD6"], 16))
        time.sleep(0.5)
        PressKey(int(key_dict["DIK_7"], 16))
        time.sleep(0.5)
        ReleaseKey(int(key_dict["DIK_7"], 16))
        time.sleep(0.5)
        PressKey(int(key_dict["DIK_7"], 16))
        time.sleep(0.5)
        ReleaseKey(int(key_dict["DIK_7"], 16))
        ReleaseKey(int(key_dict["DIK_NUMPAD6"], 16))
    time.sleep(1)


def skill(key, ct):
    print("施放%s技能..." % key)
    PressKey(int(key_dict[key], 16))
    time.sleep(0.5)
    ReleaseKey(int(key_dict[key], 16))
    PressKey(int(key_dict[key], 16))
    time.sleep(ct)
    ReleaseKey(int(key_dict[key], 16))
    time.sleep(0.5)


dist_x = findcharx()
print("現在時間:", datetime.datetime.now())
start = time.time()
print("Ready.", end="")
time.sleep(1)
print(".", end="")
time.sleep(1)
print(".")
time.sleep(1)

# 多線程
# threadObj = threading.Thread(name="MP", target=checkmana)
# threadObj.start()
# 找角色位置x

print("人物所在位置於", dist_x)
COUNTER = 0

while True:
    LOOP = 3
    for i in range(LOOP):
        print(f"{i+1} -> R")
        teleport(direction="R")
        skill("DIK_X", 4)
    for i in range(LOOP):
        print(f"{i+1} -> L")
        teleport(direction="L")
        skill("DIK_X", 4)
    # checkpos(70)
    # skill("DIK_X", 8)

    if time.time() - start >= 120:
        COUNTER += 1
        skill("DIK_8", 4.2)
        start = time.time()
    if COUNTER > 10:
        skill("DIK_T", 4.2)
        COUNTER = 0
