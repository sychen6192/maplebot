from keyboard import *
import time
from locate import *

def left(sec):
    PressKey(0xCB)
    time.sleep(sec)
    ReleaseKey(0xCB)

def right(sec):
    PressKey(0xCD)
    time.sleep(sec)
    ReleaseKey(0xCD)


def checkpos(dist_x):
    x = findcharx()
    print('目標位置:', dist_x)
    while x != dist_x:
        x = findcharx()
        mov = abs(x-dist_x)
        if x > dist_x:
            print('左移%d單位!' % mov)
            left(mov*0.125)
            time.sleep(0.5)
        else:
            print('右移%d單位!' % mov)
            right(mov * 0.125)
            time.sleep(0.5)
        if abs(dist_x-x) < 10:
            print('角色就位!')
            break